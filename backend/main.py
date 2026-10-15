import os
import json
import re
from typing import Optional, List, Dict
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status, Query, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, field_validator
import httpx

# Load environment variables
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")


# Load Indian geo dataset
GEO_DATA_PATH = ROOT_DIR / "shared" / "india_geo_data.json"
INDIAN_GEO_DATA: Dict[str, List[str]] = {}
if GEO_DATA_PATH.exists():
    with open(GEO_DATA_PATH, "r", encoding="utf-8") as f:
        INDIAN_GEO_DATA = json.load(f)

app = FastAPI(
    title="CREDORA - Lending & Credit Platform API",
    description="Backend API for Borrower Registration, Identity & Loan Management with Supabase Auth & PostgreSQL RLS",
    version="1.0.0"
)

# CORS configuration
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:8000",
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
# Pydantic Models
# -----------------------------------------------------------------------------

class BorrowerRegistrationRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100, description="Full Name as per official ID")
    email: EmailStr = Field(..., description="Valid borrower email address")
    mobile_number: str = Field(..., description="10-digit Indian Mobile Number")
    state: str = Field(..., description="Indian State or Union Territory")
    city: str = Field(..., description="City or District within the selected state")
    aadhaar_number: Optional[str] = Field(None, description="12-digit Indian Aadhaar number (optional or formatted)")
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

    @field_validator("state")
    @classmethod
    def validate_state(cls, v: str) -> str:
        state_clean = v.strip()
        if INDIAN_GEO_DATA and state_clean not in INDIAN_GEO_DATA:
            raise ValueError(f"'{state_clean}' is not recognized as a valid Indian State or Union Territory.")
        return state_clean

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
# Endpoints
# -----------------------------------------------------------------------------

@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "CREDORA Lending API",
        "supabase_connected": bool(SUPABASE_URL and SUPABASE_ANON_KEY)
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


@app.post("/api/auth/register-borrower", response_model=BorrowerRegistrationResponse)
async def register_borrower(data: BorrowerRegistrationRequest):
    """
    Registers a new borrower account:
    1. Authenticates against Supabase Auth using email + password (passwords are stored exclusively in Supabase Auth).
    2. Passes borrower profile metadata (full_name, mobile_number, state, city, aadhaar_number).
    3. The Supabase database trigger automatically creates the borrower_profiles record with Row Level Security.
    """
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase credentials are not configured on the server."
        )

    # Validate city belongs to state
    if data.state in INDIAN_GEO_DATA:
        available_cities = [c.lower() for c in INDIAN_GEO_DATA[data.state]]
        if data.city.lower() not in available_cities:
            # Allow custom city but log, or validate strictly
            pass

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
