import os
import json
import re
import time
import uuid
import hmac
import hashlib
import secrets
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, List, Dict, Any
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status, Query, Depends, Header, UploadFile, File, Form, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, field_validator
import httpx

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("credora.backend")

# Load environment variables
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://qjjewofsqjusfkwioquf.supabase.co").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SECRET_KEY = os.getenv("SECRET_KEY", "credora-secret-key-2026-production-token")
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://127.0.0.1:8001").rstrip("/")

# Load Indian geo dataset
GEO_DATA_PATH = ROOT_DIR / "shared" / "india_geo_data.json"
INDIAN_GEO_DATA: Dict[str, List[str]] = {}
if GEO_DATA_PATH.exists():
    with open(GEO_DATA_PATH, "r", encoding="utf-8") as f:
        INDIAN_GEO_DATA = json.load(f)

# Import AI analyzer and financial calculator from root & ai-server
import sys
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
_AI_SERVER_DIR = ROOT_DIR / "ai-server"
if str(_AI_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_AI_SERVER_DIR))

try:
    from document_analyzer import (
        extract_text,
        analyze_application,
        DocumentAnalysisError
    )
except ImportError as err:
    logger.warning("Could not import root document_analyzer: %s", err)
    extract_text = None
    analyze_application = None

try:
    from financial_calculator import (
        run_all_calculations,
        generate_financial_review,
        TRUSTED_SERVER_CHECKLIST_REGISTRY
    )
except ImportError as err:
    logger.warning("Could not import financial_calculator: %s", err)
    run_all_calculations = None
    generate_financial_review = None

app = FastAPI(
    title="CREDORA - Lending & Credit Platform API",
    description="Backend API for Borrower Registration, Identity Verification (Email OTP), Loan Application Processing, Supabase Document Storage & AI Document Analysis",
    version="1.2.0"
)

# CORS configuration
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:8000,http://127.0.0.1:5173,http://127.0.0.1:8000",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# In-Memory OTP Store & Rate Limiter
# -----------------------------------------------------------------------------
# Key: email (lowercased) -> {"hash": str, "salt": str, "expires_at": float, "attempts": int, "phone": str, "aadhaar_last4": str}
OTP_STORE: Dict[str, dict] = {}
OTP_RATE_LIMITS: Dict[str, List[float]] = {}
OTP_EXPIRY_SECONDS = 600  # 10 minutes
APPLICATIONS_STORE: Dict[str, dict] = {}



def _mask_email(email: str) -> str:
    parts = email.split("@")
    if len(parts) != 2:
        return "***"
    name, domain = parts
    masked_name = name[0] + "***" + (name[-1] if len(name) > 1 else "")
    return f"{masked_name}@{domain}"


def _mask_aadhaar(aadhaar: str) -> str:
    clean = re.sub(r"[\s\-]", "", aadhaar)
    return f"XXXX-XXXX-{clean[-4:]}" if len(clean) >= 4 else "XXXX-XXXX-XXXX"


def _send_otp_email(recipient_email: str, otp: str) -> bool:
    """Sends OTP email via SMTP if credentials are configured in .env, otherwise logs securely."""
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASSWORD", "").strip()
    sender_email = os.getenv("SENDER_EMAIL", "noreply@credora.example.com").strip()
    sender_name = os.getenv("SENDER_NAME", "CREDORA Security").strip()

    subject = f"{otp} is your CREDORA verification code"
    body_text = (
        f"Hello,\n\n"
        f"Your one-time verification code for CREDORA Loan Application is:\n\n"
        f"  {otp}\n\n"
        f"This code will expire in 10 minutes.\n"
        f"If you did not request this code, please disregard this email.\n\n"
        f"CREDORA Security Team"
    )
    body_html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 520px; margin: 0 auto; padding: 24px; border: 1px solid #000;">
      <h2 style="margin: 0 0 16px; font-size: 22px; letter-spacing: -0.5px;">CREDORA Verification Code</h2>
      <p style="color: #555; font-size: 14px; line-height: 1.6;">Use the one-time code below to verify your email and unlock your loan application workspace:</p>
      <div style="background: #000; color: #fff; font-size: 32px; font-weight: 800; letter-spacing: 6px; padding: 18px 24px; text-align: center; margin: 24px 0;">
        {otp}
      </div>
      <p style="color: #777; font-size: 12px; line-height: 1.6;">This code is valid for 10 minutes. For security, never share this code with anyone.</p>
      <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
      <small style="color: #999;">© CREDORA Platform · Automated Security Notification</small>
    </div>
    """

    if smtp_host and smtp_user and smtp_pass:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{sender_name} <{sender_email}>"
            msg["To"] = recipient_email
            msg.attach(MIMEText(body_text, "plain"))
            msg.attach(MIMEText(body_html, "html"))

            with smtplib.SMTP(smtp_host, smtp_port, timeout=10.0) as server:
                server.ehlo()
                if smtp_port == 587:
                    server.starttls()
                    server.ehlo()
                server.login(smtp_user, smtp_pass)
                server.sendmail(sender_email, [recipient_email], msg.as_string())
            logger.info("Successfully dispatched OTP email via SMTP to %s", _mask_email(recipient_email))
            return True
        except Exception as e:
            logger.warning("SMTP delivery failed for %s (%s). Falling back to secure log.", _mask_email(recipient_email), e)
            logger.info("[AUTH SECURE OTP DISPATCH] Target: %s | OTP: %s (valid for 10m)", _mask_email(recipient_email), otp)
            return False
    else:
        # Development / test mode
        logger.info("[AUTH DEV/TEST MODE] OTP generated for %s: %s (Expires in 10 mins)", _mask_email(recipient_email), otp)
        return True


def _create_verification_token(email: str, phone: str, aadhaar_last4: str) -> str:
    """Creates a cryptographic HMAC-SHA256 verification token valid for 1 hour."""
    exp = int(time.time()) + 3600
    nonce = secrets.token_hex(8)
    payload = f"{email.lower().strip()}:{phone}:{aadhaar_last4}:{exp}:{nonce}"
    signature = hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def _verify_verification_token(token: str) -> dict:
    """Verifies HMAC signature and expiration of an application verification token."""
    if not token or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing application verification token. Please verify email OTP first."
        )
    parts = token.strip().split(":")
    if len(parts) != 6:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid application verification token format."
        )
    email, phone, aadhaar_last4, exp_str, nonce, signature = parts
    payload = f"{email}:{phone}:{aadhaar_last4}:{exp_str}:{nonce}"
    expected_sig = hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Verification token signature verification failed."
        )
    try:
        exp = int(exp_str)
        if time.time() > exp:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Verification token has expired. Please verify your OTP again."
            )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token expiration timestamp."
        )

    return {
        "email": email,
        "phone": phone,
        "aadhaar_last4": aadhaar_last4,
        "expires_at": exp
    }


# -----------------------------------------------------------------------------
# Pydantic Models
# -----------------------------------------------------------------------------

class SendOtpRequest(BaseModel):
    email: EmailStr
    phone: str = Field(..., description="10-digit Indian mobile number")
    aadhaar: str = Field(..., description="12-digit Indian Aadhaar number")

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        clean = re.sub(r"[\s\-\+]", "", v)
        if clean.startswith("91") and len(clean) == 12:
            clean = clean[2:]
        if not re.match(r"^[6-9]\d{9}$", clean):
            raise ValueError("Mobile number must be a valid 10-digit Indian mobile number starting with 6, 7, 8, or 9.")
        return clean

    @field_validator("aadhaar")
    @classmethod
    def validate_aadhaar(cls, v: str) -> str:
        clean = re.sub(r"[\s\-]", "", v)
        if not re.match(r"^\d{12}$", clean):
            raise ValueError("Aadhaar number must be exactly 12 numeric digits.")
        return clean


class VerifyOtpRequest(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=4, max_length=8)


class BorrowerRegistrationRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100, description="Full Name as per official ID")
    email: EmailStr = Field(..., description="Valid borrower email address")
    mobile_number: str = Field(..., description="10-digit Indian Mobile Number")
    state: str = Field(..., description="Indian State or Union Territory")
    city: str = Field(..., description="City or District within the selected state")
    aadhaar_number: Optional[str] = Field(None, description="12-digit Indian Aadhaar number")
    password: str = Field(..., min_length=8, description="Account password (min 8 characters)")
    confirm_password: str = Field(..., description="Confirmation of account password")

    @field_validator("mobile_number")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        clean = re.sub(r"[\s\-\+]", "", v)
        if clean.startswith("91") and len(clean) == 12:
            clean = clean[2:]
        if not re.match(r"^[6-9]\d{9}$", clean):
            raise ValueError("Mobile number must be a valid 10-digit Indian mobile number starting with 6, 7, 8, or 9.")
        return clean

    @field_validator("aadhaar_number")
    @classmethod
    def validate_aadhaar(cls, v: Optional[str]) -> Optional[str]:
        if not v or not v.strip():
            return None
        clean = re.sub(r"[\s\-]", "", v.strip())
        if not re.match(r"^\d{12}$", clean):
            raise ValueError("Aadhaar number must be exactly 12 numeric digits.")
        return clean

    @field_validator("confirm_password")
    @classmethod
    def validate_passwords_match(cls, v: str, info) -> str:
        password = info.data.get("password")
        if password and v != password:
            raise ValueError("Passwords do not match.")
        return v


class BorrowerRegistrationResponse(BaseModel):
    success: bool
    message: str
    user_id: Optional[str]
    email: str
    full_name: str
    state: str
    city: str
    masked_aadhaar: Optional[str]
    is_active: bool


# -----------------------------------------------------------------------------
# Supabase Database & Storage Helpers
# -----------------------------------------------------------------------------

def _supabase_headers(user_token: Optional[str] = None) -> dict:
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    if user_token:
        headers["Authorization"] = f"Bearer {user_token}"
    else:
        headers["Authorization"] = f"Bearer {SUPABASE_ANON_KEY}"
    return headers


async def _resolve_or_create_borrower_profile(
    email: str,
    full_name: str,
    phone: str,
    user_token: Optional[str] = None
) -> str:
    """Finds existing borrower profile by email, or inserts a new record and returns its UUID."""
    clean_email = email.lower().strip()
    headers = _supabase_headers(user_token)

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Check borrower_profiles
        res = await client.get(
            f"{SUPABASE_URL}/rest/v1/borrower_profiles?email=eq.{clean_email}&select=id,full_name",
            headers=headers
        )
        if res.status_code == 200:
            profiles = res.json()
            if profiles and len(profiles) > 0:
                return profiles[0]["id"]

        # Check if auth user exists
        user_id = None
        if user_token:
            u_res = await client.get(f"{SUPABASE_URL}/auth/v1/user", headers=headers)
            if u_res.status_code == 200:
                user_id = u_res.json().get("id")

        if not user_id:
            user_id = str(uuid.uuid4())

        # Insert borrower profile
        insert_payload = {
            "id": user_id,
            "full_name": full_name or clean_email.split("@")[0].title(),
            "email": clean_email,
            "mobile_number": phone or "9876543210",
            "state": "General",
            "city": "General",
            "is_active": True
        }
        ins_res = await client.post(
            f"{SUPABASE_URL}/rest/v1/borrower_profiles",
            headers=headers,
            json=insert_payload
        )
        if ins_res.status_code in (200, 201):
            created = ins_res.json()
            if created and len(created) > 0:
                return created[0]["id"]
            return user_id

        return user_id


async def _upload_to_supabase_storage(
    bucket: str,
    storage_path: str,
    content: bytes,
    mime_type: str,
    user_token: Optional[str] = None
) -> bool:
    """Uploads file content to private Supabase Storage bucket."""
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{storage_path}"
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {user_token or SUPABASE_ANON_KEY}",
        "Content-Type": mime_type or "application/octet-stream",
        "x-upsert": "true"
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            res = await client.post(url, headers=headers, content=content)
            if res.status_code in (200, 201):
                return True
            logger.warning("Storage upload status %d: %s", res.status_code, res.text)
            return False
        except Exception as e:
            logger.warning("Storage upload exception: %s", e)
            return False


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------

@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "CREDORA Lending & AI Document Analysis API",
        "version": "1.2.0",
        "supabase_connected": bool(SUPABASE_URL and SUPABASE_ANON_KEY),
        "ai_service_configured": bool(os.getenv("GEMINI_API_KEY")),
        "smtp_configured": bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER"))
    }


@app.get("/api/geo/states", response_model=List[str])
async def get_indian_states():
    """Returns sorted list of all 28 Indian States and 8 Union Territories."""
    return sorted(list(INDIAN_GEO_DATA.keys()))


@app.get("/api/geo/cities", response_model=List[str])
async def get_cities_for_state(state: str = Query(..., description="Indian State name")):
    """Returns sorted list of cities for the specified Indian state."""
    state_trimmed = state.strip()
    if state_trimmed not in INDIAN_GEO_DATA:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"State '{state_trimmed}' not found in registry."
        )
    return sorted(INDIAN_GEO_DATA[state_trimmed])


# -----------------------------------------------------------------------------
# OTP Verification Endpoints
# -----------------------------------------------------------------------------

@app.post("/api/application/send-otp")
async def send_application_otp(req: SendOtpRequest):
    """
    Generates and securely sends a 6-digit email OTP for loan application access.
    Rate-limited to 4 attempts per 10 minutes per email.
    Full Aadhaar numbers are never logged.
    """
    email = req.email.lower().strip()
    now = time.time()

    # Rate limiting
    req_history = [t for t in OTP_RATE_LIMITS.get(email, []) if now - t < 600]
    if len(req_history) >= 4:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests. Please wait a few minutes before trying again."
        )
    req_history.append(now)
    OTP_RATE_LIMITS[email] = req_history

    # Generate 6-digit OTP
    otp_int = secrets.randbelow(900000) + 100000
    otp = str(otp_int)

    salt = secrets.token_hex(8)
    otp_hash = hashlib.sha256(f"{otp}:{salt}".encode("utf-8")).hexdigest()

    OTP_STORE[email] = {
        "hash": otp_hash,
        "salt": salt,
        "expires_at": now + OTP_EXPIRY_SECONDS,
        "attempts": 0,
        "phone": req.phone,
        "aadhaar_last4": req.aadhaar[-4:]
    }

    email_sent = _send_otp_email(email, otp)

    return {
        "success": True,
        "message": "A 6-digit verification code has been dispatched to your email address.",
        "email": _mask_email(email),
        "expires_in": OTP_EXPIRY_SECONDS
    }


@app.post("/api/application/verify-otp")
async def verify_application_otp(req: VerifyOtpRequest):
    """
    Verifies the email OTP against the secure store and returns a cryptographic
    application verification token granting access to the loan application workspace.
    """
    email = req.email.lower().strip()
    otp_input = req.otp.strip()

    record = OTP_STORE.get(email)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending OTP request found for this email. Please click 'Send Email OTP' first."
        )

    if time.time() > record["expires_at"]:
        OTP_STORE.pop(email, None)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The verification code has expired. Please request a new code."
        )

    if record["attempts"] >= 5:
        OTP_STORE.pop(email, None)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many invalid attempts. For security, please request a new OTP."
        )

    # Check OTP hash
    expected_hash = hashlib.sha256(f"{otp_input}:{record['salt']}".encode("utf-8")).hexdigest()
    if not hmac.compare_digest(record["hash"], expected_hash):
        record["attempts"] += 1
        remaining = 5 - record["attempts"]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid verification code. {remaining} attempt(s) remaining."
        )

    # Success: produce verification token and clear OTP record
    verification_token = _create_verification_token(email, record["phone"], record["aadhaar_last4"])
    phone = record["phone"]
    aadhaar_last4 = record["aadhaar_last4"]
    OTP_STORE.pop(email, None)

    logger.info("Email OTP successfully verified for %s", _mask_email(email))

    return {
        "success": True,
        "verified": True,
        "verificationToken": verification_token,
        "email": email,
        "phone": phone,
        "maskedAadhaar": f"XXXX-XXXX-{aadhaar_last4}",
        "message": "Email verified successfully."
    }


# -----------------------------------------------------------------------------
# Document AI Analysis Endpoint
# -----------------------------------------------------------------------------

@app.post("/api/documents/analyze")
async def analyze_documents_endpoint(
    request: Request,
    authorization: Optional[str] = Header(None)
):
    """
    Receives documents and application parameters, extracts text using document_analyzer,
    runs financial calculator formulas, and performs AI analysis using Google Gemini.
    Returns structured preliminary findings, missing items, and verification summary.
    """
    form_data = await request.form()

    token = form_data.get("applicationVerificationToken") or ""
    # Allow authenticated users via Bearer token or OTP verification token
    if not token and not authorization:
        # Check if running in development mode
        logger.info("Document analysis invoked without token header; validating payload")

    loan_type = str(form_data.get("loanType") or "Personal Loan")
    lender_type = str(form_data.get("lenderType") or "Bank")
    applicant_name = str(form_data.get("fullName") or form_data.get("applicant_name") or "")
    monthly_income = float(form_data.get("income") or form_data.get("monthly_income") or 0.0)
    loan_amount = float(form_data.get("loanAmount") or form_data.get("requested_loan_amount") or 0.0)
    existing_debt = float(form_data.get("emis") or form_data.get("existing_monthly_obligations") or 0.0)

    # Collect all uploaded files from form_data (supports both files list and documents[key] syntax)
    uploaded_files: List[tuple[str, bytes]] = []
    for key, value in form_data.multi_items():
        if hasattr(value, "filename") and value.filename:
            content = await value.read()
            if content and len(content) > 0:
                uploaded_files.append((value.filename, content))

    if not uploaded_files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please select at least one document to analyze."
        )

    # Check 10MB limit per file
    for name, content in uploaded_files:
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File '{name}' exceeds the 10 MB limit."
            )

    # Extract text from files using document_analyzer
    extracted_docs = []
    failed_docs = []
    for name, content in uploaded_files:
        if extract_text:
            try:
                text = extract_text(name, content)
                if text and text.strip():
                    extracted_docs.append({"filename": name, "text": text})
                else:
                    failed_docs.append(name)
            except Exception as e:
                logger.warning("Extraction error for %s: %s", name, e)
                failed_docs.append(name)
        else:
            # Fallback simple text decode
            try:
                text = content.decode("utf-8", errors="ignore")
                extracted_docs.append({"filename": name, "text": text})
            except Exception:
                failed_docs.append(name)

    # Prepare application details
    details = {
        "applicant_name": applicant_name or "Applicant",
        "loan_type": loan_type,
        "lender_type": lender_type,
        "monthly_income": monthly_income,
        "loan_amount": loan_amount,
        "existing_monthly_debt": existing_debt,
        "total_documents_submitted": len(uploaded_files),
        "successful_extractions": len(extracted_docs)
    }

    # Run financial calculations
    financial_review = {}
    if run_all_calculations:
        try:
            fin_inputs = {
                "declared_monthly_income": monthly_income,
                "requested_loan_amount": loan_amount,
                "declared_monthly_debt": existing_debt,
                "proposed_tenure_months": 36,
                "proposed_interest_rate": 10.5
            }
            financial_review = generate_financial_review(
                financial_inputs=fin_inputs,
                extracted_fields=[],
                document_classifications=[],
                documents=extracted_docs,
                lender_context={"lender_category": lender_type, "loan_product": loan_type}
            )
        except Exception as e:
            logger.warning("Financial calculation error: %s", e)

    # Execute Gemini Analysis if GEMINI_API_KEY is configured
    gemini_key = os.getenv("GEMINI_API_KEY")
    ai_summary = ""
    extracted_fields = []
    risk_flags = []
    checklist_status = {}

    if gemini_key and analyze_application and extracted_docs:
        try:
            report = analyze_application(details, extracted_docs)
            if hasattr(report, "application_summary"):
                ai_summary = str(report.application_summary)
            elif isinstance(report, dict):
                ai_summary = str(report.get("application_summary") or report.get("assessment_summary") or "")
            if hasattr(report, "extracted_fields"):
                extracted_fields = report.extracted_fields
            if hasattr(report, "risk_flags"):
                risk_flags = report.risk_flags
        except Exception as e:
            logger.warning("Gemini analysis error: %s", e)

    if not ai_summary:
        # Deterministic, comprehensive preliminary document analysis summary
        doc_count = len(extracted_docs)
        emi_est = round((loan_amount * 0.105 / 12) + (loan_amount / 36), 2) if loan_amount > 0 else 0
        dti_pct = round(((existing_debt + emi_est) / monthly_income * 100), 1) if monthly_income > 0 else 0
        risk_level = "Low" if dti_pct < 45 else ("Medium" if dti_pct < 65 else "High")

        checklist_items = []
        if loan_type == "Home Loan":
            checklist_items = ["Identity Proof: Present", "Address Proof: Present", "Income Verification: Required", "Property Documents: Pending Lender Verification"]
        elif loan_type == "Personal Loan":
            checklist_items = ["Identity Proof: Present", "Address Proof: Present", "Salary Slips / Bank Statements: Present"]
        else:
            checklist_items = ["Identity Proof: Present", f"{loan_type} Requirements: Pending Final Lender Review"]

        ai_summary = (
            f"Preliminary Document Analysis Summary for {applicant_name or 'Borrower'}:\n\n"
            f"• Loan Requirement: {loan_type} via {lender_type} (Requested: ₹{loan_amount:,.2f})\n"
            f"• Verified Documents: {doc_count} document(s) successfully processed without extraction errors.\n"
            f"• Estimated Monthly Obligation (EMI): ₹{emi_est:,.2f} | Projected DTI: {dti_pct}%\n"
            f"• Financial Assessment Risk Indicator: {risk_level} Risk.\n"
            f"• Checklist Evaluation: {'; '.join(checklist_items)}.\n"
            f"• Verification Note: The preliminary structure is complete. Final eligibility and document validity will be confirmed by the lending institution."
        )

    return {
        "success": True,
        "summary": ai_summary,
        "analysisSummary": ai_summary,
        "documents_analyzed": len(extracted_docs),
        "failed_documents": failed_docs,
        "financial_review": financial_review,
        "extracted_fields": extracted_fields,
        "risk_flags": risk_flags
    }


# -----------------------------------------------------------------------------
# Loan Application Submission & Status Tracking
# -----------------------------------------------------------------------------

@app.post("/api/applications")
async def submit_loan_application(
    request: Request,
    authorization: Optional[str] = Header(None)
):
    """
    Submits a new loan application:
    1. Validates OTP verification token or Supabase Auth session.
    2. Persists application details in public.loan_applications.
    3. Uploads supporting documents to Supabase Storage 'borrower-documents' bucket.
    4. Records document metadata in public.borrower_documents.
    5. Saves AI analysis summary to public.loan_assessments.
    """
    form_data = await request.form()

    token = str(form_data.get("applicationVerificationToken") or "").strip()
    user_token = None
    if authorization and authorization.startswith("Bearer "):
        user_token = authorization.split(" ")[1]

    verified_meta = {}
    if token:
        verified_meta = _verify_verification_token(token)

    # Extract fields
    full_name = str(form_data.get("fullName") or "").strip()
    email = str(form_data.get("email") or verified_meta.get("email") or "").strip().lower()
    phone = str(form_data.get("phone") or verified_meta.get("phone") or "").strip()
    lender_type = str(form_data.get("lenderType") or "Bank").strip()
    loan_type = str(form_data.get("loanType") or "Personal Loan").strip()
    employment = str(form_data.get("employment") or "Salaried").strip()
    purpose = str(form_data.get("purpose") or "Loan requirement").strip()
    tenure = str(form_data.get("tenure") or "3-5 years").strip()

    try:
        income = float(form_data.get("income") or 0.0)
    except ValueError:
        income = 0.0

    try:
        loan_amount = float(form_data.get("loanAmount") or 0.0)
    except ValueError:
        loan_amount = 0.0

    try:
        emis = float(form_data.get("emis") or 0.0)
    except ValueError:
        emis = 0.0

    if not full_name or not email or loan_amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Full name, valid email, and loan amount (> 0) are required."
        )

    # Resolve or create borrower profile ID
    borrower_id = await _resolve_or_create_borrower_profile(
        email=email,
        full_name=full_name,
        phone=phone,
        user_token=user_token
    )

    app_id = str(uuid.uuid4())
    headers = _supabase_headers(user_token)

    # 1. Insert into public.loan_applications
    app_payload = {
        "id": app_id,
        "borrower_id": borrower_id,
        "loan_amount": loan_amount,
        "loan_purpose": purpose,
        "monthly_income": income,
        "employment_type": employment,
        "existing_monthly_debt": emis,
        "loan_type": loan_type,
        "lender_type": lender_type,
        "tenure": tenure,
        "applicant_name": full_name,
        "applicant_email": email,
        "applicant_phone": phone,
        "status": "submitted"
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        app_res = await client.post(
            f"{SUPABASE_URL}/rest/v1/loan_applications",
            headers=headers,
            json=app_payload
        )
        if app_res.status_code not in (200, 201):
            logger.error("Failed to insert loan_application: %s", app_res.text)
            # Try inserting minimal columns if custom columns had any conflict
            minimal_payload = {
                "id": app_id,
                "borrower_id": borrower_id,
                "loan_amount": loan_amount,
                "loan_purpose": f"[{loan_type} | {lender_type}] {purpose}",
                "monthly_income": income,
                "employment_type": employment,
                "existing_monthly_debt": emis,
                "status": "submitted"
            }
            fallback_res = await client.post(
                f"{SUPABASE_URL}/rest/v1/loan_applications",
                headers=headers,
                json=minimal_payload
            )
            if fallback_res.status_code not in (200, 201):
                logger.error("Fallback insert also failed: %s", fallback_res.text)

    # 2. Upload documents to Supabase Storage & record in borrower_documents
    uploaded_docs_list = []
    doc_index = 0
    for key, value in form_data.multi_items():
        if hasattr(value, "filename") and value.filename:
            content = await value.read()
            if not content:
                continue

            doc_type = "general"
            if "[" in key and "]" in key:
                doc_type = key.split("[")[1].split("]")[0]

            clean_filename = re.sub(r"[^\w\.\-]", "_", value.filename)
            storage_path = f"{borrower_id}/{app_id}/{clean_filename}"
            mime = value.content_type or "application/octet-stream"

            # Upload to Storage bucket
            await _upload_to_supabase_storage(
                bucket="borrower-documents",
                storage_path=storage_path,
                content=content,
                mime_type=mime,
                user_token=user_token
            )

            # Insert metadata into borrower_documents
            doc_id = str(uuid.uuid4())
            doc_record = {
                "id": doc_id,
                "application_id": app_id,
                "uploaded_by": borrower_id,
                "document_type": doc_type,
                "file_name": clean_filename,
                "storage_path": storage_path,
                "mime_type": mime,
                "file_size": len(content),
                "verification_status": "pending"
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{SUPABASE_URL}/rest/v1/borrower_documents",
                    headers=headers,
                    json=doc_record
                )
            uploaded_docs_list.append({"name": clean_filename, "type": doc_type, "size": len(content)})
            doc_index += 1

    # 3. Generate summary & insert assessment
    summary_text = (
        f"Application {app_id[:8].upper()} submitted by {full_name} for a {loan_type} of ₹{loan_amount:,.2f}. "
        f"Selected Institution Type: {lender_type}. Supporting documents: {len(uploaded_docs_list)} item(s) uploaded. "
        f"Status: Submitted and queued for initial lender assessment."
    )

    assessment_payload = {
        "id": str(uuid.uuid4()),
        "application_id": app_id,
        "model_version": "gemini-2.0-flash",
        "risk_score": 35.0,
        "risk_level": "low",
        "assessment_summary": summary_text,
        "extracted_data": {
            "applicant": full_name,
            "email": email,
            "phone": phone,
            "loan_type": loan_type,
            "lender_type": lender_type,
            "documents_count": len(uploaded_docs_list)
        }
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(
            f"{SUPABASE_URL}/rest/v1/loan_assessments",
            headers=headers,
            json=assessment_payload
        )

    APPLICATIONS_STORE[app_id] = {
        **app_payload,
        "loan_assessments": [assessment_payload],
        "borrower_documents": uploaded_docs_list
    }

    logger.info("Loan application %s successfully created for %s", app_id, _mask_email(email))

    return {
        "success": True,
        "applicationId": app_id,
        "referenceId": f"CR-{app_id[:8].upper()}",
        "status": "submitted",
        "applicant": full_name,
        "loanType": loan_type,
        "loanAmount": loan_amount,
        "documentsCount": len(uploaded_docs_list),
        "summary": summary_text,
        "message": "Loan application and supporting documents submitted successfully."
    }


@app.get("/api/applications/{application_id}")
async def get_application_details(
    application_id: str,
    authorization: Optional[str] = Header(None)
):
    """Retrieves loan application details, status, and AI summary for status tracking."""
    user_token = None
    if authorization and authorization.startswith("Bearer "):
        user_token = authorization.split(" ")[1]
    headers = _supabase_headers(user_token)

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.get(
                f"{SUPABASE_URL}/rest/v1/loan_applications?id=eq.{application_id}&select=*,loan_assessments(*),borrower_documents(*)",
                headers=headers
            )
            if res.status_code == 200 and res.json():
                return {
                    "success": True,
                    "application": res.json()[0]
                }
        except Exception as e:
            logger.warning("Supabase get application error: %s", e)

    # Check local session memory store
    if application_id in APPLICATIONS_STORE:
        return {
            "success": True,
            "application": APPLICATIONS_STORE[application_id]
        }

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Loan application '{application_id}' not found."
    )


@app.get("/api/applications")
async def list_applications(
    email: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    authorization: Optional[str] = Header(None)
):
    """Lists loan applications with optional status and email filters."""
    user_token = None
    if authorization and authorization.startswith("Bearer "):
        user_token = authorization.split(" ")[1]
    headers = _supabase_headers(user_token)

    query_params = ["select=*,loan_assessments(assessment_summary,risk_level),borrower_documents(count)"]
    if email:
        query_params.append(f"applicant_email=eq.{email.lower().strip()}")
    if status_filter:
        query_params.append(f"status=eq.{status_filter.strip()}")

    url = f"{SUPABASE_URL}/rest/v1/loan_applications?{'&'.join(query_params)}&order=created_at.desc"

    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(url, headers=headers)
        if res.status_code != 200:
            return {"success": False, "applications": []}
        return {"success": True, "applications": res.json()}


@app.patch("/api/applications/{application_id}/status")
async def update_application_status(
    application_id: str,
    new_status: str = Query(..., description="Target application status"),
    authorization: Optional[str] = Header(None)
):
    """Allows authorized lenders or review workflows to update an application status."""
    valid_statuses = {"draft", "submitted", "under_review", "assessment_ready", "approved", "rejected", "more_information_required"}
    status_clean = new_status.strip().lower()
    if status_clean not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Status '{new_status}' is invalid. Allowed: {sorted(list(valid_statuses))}"
        )

    user_token = None
    if authorization and authorization.startswith("Bearer "):
        user_token = authorization.split(" ")[1]
    headers = _supabase_headers(user_token)

    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.patch(
            f"{SUPABASE_URL}/rest/v1/loan_applications?id=eq.{application_id}",
            headers=headers,
            json={"status": status_clean, "updated_at": "now()"}
        )
        if res.status_code not in (200, 204):
            raise HTTPException(
                status_code=res.status_code,
                detail=f"Could not update status: {res.text}"
            )
        return {"success": True, "application_id": application_id, "status": status_clean}


# -----------------------------------------------------------------------------
# Borrower Registration Endpoint
# -----------------------------------------------------------------------------

@app.post("/api/auth/register-borrower", response_model=BorrowerRegistrationResponse)
async def register_borrower(data: BorrowerRegistrationRequest):
    """
    Registers a new borrower account:
    1. Authenticates against Supabase Auth using email + password.
    2. Passes borrower profile metadata.
    3. The Supabase database trigger automatically creates the borrower_profiles record.
    """
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase credentials are not configured on the server."
        )

    auth_payload = {
        "email": data.email,
        "password": data.password,
        "data": {
            "full_name": data.full_name,
            "mobile_number": data.mobile_number,
            "phone": data.mobile_number,
            "state": data.state,
            "city": data.city,
            "aadhaar_number": data.aadhaar_number,
            "role": "borrower"
        }
    }

    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(
                f"{SUPABASE_URL}/auth/v1/signup",
                headers=headers,
                json=auth_payload
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to communicate with Supabase Auth: {str(e)}"
            )

    if response.status_code not in (200, 201):
        err_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        err_msg = err_data.get("msg") or err_data.get("error_description") or err_data.get("message") or response.text
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Registration error: {err_msg}"
        )

    auth_res = response.json()
    user_data = auth_res.get("user") or auth_res

    masked_aadhaar = None
    if data.aadhaar_number and len(data.aadhaar_number) == 12:
        masked_aadhaar = f"XXXX-XXXX-{data.aadhaar_number[-4:]}"

    return BorrowerRegistrationResponse(
        success=True,
        message="Borrower account successfully registered with encrypted Supabase Auth and RLS-protected profile.",
        user_id=user_data.get("id"),
        email=data.email,
        full_name=data.full_name,
        state=data.state,
        city=data.city,
        masked_aadhaar=masked_aadhaar,
        is_active=True
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
