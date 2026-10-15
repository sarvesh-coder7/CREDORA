"""
Cross-Document Consistency and Address Verification Module for CREDORA.

Provides deterministic, rule-based cross-document comparisons for loan applications:
1. Identity consistency (name normalization, token permutations, initials, titles, typo detection)
2. Address consistency (component normalization, abbreviations, PIN codes, current vs permanent addresses)
3. Date & employment consistency (employer normalization, acronyms, document recency windows, expiry)
4. Financial consistency (declared vs gross/net salary, bank credit reconciliation, account holder names)
5. Duplicate & contradictory document detection (SHA-256 hashing, overlapping statement periods)

Rules:
- Pure deterministic logic kept separate from AI interpretation.
- AI confidence scores never override deterministic mismatches.
- Neutral audit terminology (no accusations of fraud or forgery).
- No invented missing values; missing fields are clearly reported.
- Sensitive identifiers (Aadhaar, PAN, card, bank accounts) are masked.
- Configurable matching tolerances with documented defaults.
- Postal address checks include disclaimers against physical geocoding.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any


# ── Configuration & Configurable Matching Tolerances ─────────────────────────

@dataclass
class CrossDocConfig:
    """
    Configurable parameters and tolerances for deterministic cross-document checks.
    All defaults are documented and can be overridden per lender/product context.
    """
    # Name matching tolerances
    name_fuzzy_threshold: float = 0.85
    name_partial_threshold: float = 0.70
    
    # Financial tolerances
    salary_variance_tolerance_pct: float = 10.0   # Up to 10% variance tolerated without warning
    large_salary_variance_pct: float = 25.0       # >25% variance flagged as high severity
    bank_salary_credit_tolerance_pct: float = 5.0 # 5% tolerance between net salary and bank credit
    
    # Recency / Expiry windows (in calendar days)
    salary_slip_max_age_days: int = 90            # Standard 3 months
    bank_statement_max_age_days: int = 180        # Standard 6 months
    itr_max_age_days: int = 730                   # Standard 2 assessment years
    
    # Address policy
    allow_current_permanent_difference: bool = True  # Current and permanent addresses legitimately differ
    address_token_overlap_threshold: float = 0.55   # Minimum token overlap for normalized match
    address_partial_overlap_threshold: float = 0.25 # Minimum token overlap for partial match
    
    # Duplicate checking
    enable_hash_duplicate_check: bool = True

    @classmethod
    def from_lender_context(cls, lender_context: dict[str, Any] | None) -> CrossDocConfig:
        """Create config overriding defaults from lender_context if provided."""
        cfg = cls()
        if not lender_context or not isinstance(lender_context, dict):
            return cfg
        if "salary_slip_recency_days" in lender_context:
            try:
                cfg.salary_slip_max_age_days = int(lender_context["salary_slip_recency_days"])
            except (ValueError, TypeError):
                pass
        if "bank_statement_recency_days" in lender_context:
            try:
                cfg.bank_statement_max_age_days = int(lender_context["bank_statement_recency_days"])
            except (ValueError, TypeError):
                pass
        if "itr_recency_days" in lender_context:
            try:
                cfg.itr_max_age_days = int(lender_context["itr_recency_days"])
            except (ValueError, TypeError):
                pass
        if "salary_variance_tolerance_pct" in lender_context:
            try:
                cfg.salary_variance_tolerance_pct = float(lender_context["salary_variance_tolerance_pct"])
            except (ValueError, TypeError):
                pass
        if "allow_current_permanent_difference" in lender_context:
            cfg.allow_current_permanent_difference = bool(lender_context["allow_current_permanent_difference"])
        return cfg


# ── Sensitive Data Masking Helpers ───────────────────────────────────────────

def mask_sensitive_value(val: Any) -> Any:
    """Mask Aadhaar, PAN, Card, and Bank Account numbers in strings or structures."""
    if not isinstance(val, str):
        if isinstance(val, dict):
            return {k: mask_sensitive_value(v) for k, v in val.items()}
        if isinstance(val, list):
            return [mask_sensitive_value(item) for item in val]
        return val

    # Aadhaar (12 digits)
    text = re.sub(
        r"\b([2-9]\d{3})[\s-]?(\d{4})[\s-]?(\d{4})\b",
        r"XXXX-XXXX-\3",
        val,
    )
    # PAN (5 letters, 4 digits, 1 letter)
    text = re.sub(
        r"\b([A-Z]{5})(\d{4})([A-Z])\b",
        r"XXXXX\2\3",
        text,
        flags=re.IGNORECASE,
    )
    # Payment card (16 digits)
    text = re.sub(
        r"\b(?:\d{4}[-\s]?){3}(\d{4})\b",
        r"XXXXXXXXXXXX\1",
        text,
    )
    # Bank Account numbers (9 to 18 digits with keyword or standing pattern)
    text = re.sub(
        r"(\b(?:A/C|AC|Account|Acct|Acc)[\s#:]*)\d{5,14}(\d{4})\b",
        r"\1XXXXXXXX\2",
        text,
        flags=re.IGNORECASE,
    )
    return text


# ── Text & Token Normalization Helpers ───────────────────────────────────────

HONORIFICS = {
    "mr", "mrs", "ms", "dr", "prof", "shri", "shree", "smt", "kumari", "master",
    "md", "capt", "col", "major", "adv", "advocate",
}

CORPORATE_SUFFIXES = {
    "pvt ltd", "private limited", "pvt. ltd.", "pvt limited", "ltd", "limited",
    "llp", "inc", "corp", "corporation", "co", "company", "technologies",
    "technology", "solutions", "services", "enterprises", "india", "pvt",
}

ADDRESS_ABBREVIATIONS = {
    "rd": "road",
    "st": "street",
    "apt": "apartment",
    "apts": "apartments",
    "flr": "floor",
    "bldg": "building",
    "ngr": "nagar",
    "nr": "near",
    "opp": "opposite",
    "blk": "block",
    "no": "number",
    "no.": "number",
    "h.no": "house number",
    "h no": "house number",
    "pl": "plot",
    "plt": "plot",
    "sec": "sector",
    "sect": "sector",
    "ext": "extension",
    "mrg": "marg",
    "clny": "colony",
    "soc": "society",
    "cross": "cross",
    "main": "main",
    "comp": "compound",
}

CITY_NORMALIZATIONS = {
    "bangalore": "bengaluru",
    "bombay": "mumbai",
    "madras": "chennai",
    "calcutta": "kolkata",
    "poona": "pune",
    "baroda": "vadodara",
    "trivandrum": "thiruvananthapuram",
    "cochin": "kochi",
    "gurgaon": "gurugram",
}

STATE_CODE_MAP = {
    "ka": "karnataka",
    "mh": "maharashtra",
    "dl": "delhi",
    "tn": "tamil nadu",
    "ts": "telangana",
    "ap": "andhra pradesh",
    "wb": "west bengal",
    "gj": "gujarat",
    "rj": "rajasthan",
    "hr": "haryana",
    "kl": "kerala",
    "mp": "madhya pradesh",
    "pb": "punjab",
    "br": "bihar",
    "up": "uttar pradesh",
    "uk": "uttarakhand",
    "or": "odisha",
    "od": "odisha",
    "ga": "goa",
    "ch": "chandigarh",
    "py": "puducherry",
}


def normalize_tokens(text: str) -> list[str]:
    """Lowercase, strip non-alphanumeric punctuation, and split into clean tokens."""
    if not text:
        return []
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return [t for t in cleaned.split() if t]


def normalize_name(name: str) -> tuple[str, list[str]]:
    """
    Normalize personal name by removing honorific titles, punctuation, and extra whitespace.
    Returns (normalized_full_string, tokens_list).
    """
    if not name or not isinstance(name, str):
        return "", []
    tokens = normalize_tokens(name)
    # Filter out initial honorifics
    while tokens and tokens[0] in HONORIFICS:
        tokens.pop(0)
    return " ".join(tokens), tokens


def normalize_company_name(name: str) -> tuple[str, list[str]]:
    """
    Normalize company / employer name by stripping common corporate suffixes.
    """
    if not name or not isinstance(name, str):
        return "", []
    raw_lower = name.lower()
    # Strip multi-word suffixes first
    for sfx in ["private limited", "pvt ltd", "pvt. ltd.", "pvt limited"]:
        raw_lower = re.sub(rf"\b{re.escape(sfx)}\b", "", raw_lower)
    tokens = normalize_tokens(raw_lower)
    # Strip remaining single-word corporate suffixes
    filtered = [t for t in tokens if t not in {"ltd", "limited", "inc", "corp", "llp", "co", "company", "pvt"}]
    return " ".join(filtered), filtered


def normalize_address(address: str) -> dict[str, Any]:
    """
    Normalize address components, expand common abbreviations, and extract PIN code.
    Preserves original string for audit traceability.
    """
    if not address or not isinstance(address, str):
        return {
            "original": address or "",
            "normalized_text": "",
            "tokens": [],
            "pin_code": None,
            "city": None,
            "state": None,
        }

    raw = address.strip()
    # Normalize initials like M.G. Rd -> MG Road
    raw = re.sub(r"\bm[\.\s]+g[\.\s]+", "mg ", raw, flags=re.IGNORECASE)

    # Extract 6-digit Indian PIN code
    pin_match = re.search(r"\b([1-9]\d{5})\b", raw)
    pin_code = pin_match.group(1) if pin_match else None

    # Replace abbreviations
    tokens = normalize_tokens(raw)
    expanded_tokens = [ADDRESS_ABBREVIATIONS.get(t, t) for t in tokens]

    # City & State identification
    detected_city = None
    detected_state = None

    for t in expanded_tokens:
        if t in CITY_NORMALIZATIONS:
            detected_city = CITY_NORMALIZATIONS[t]
        elif t in CITY_NORMALIZATIONS.values():
            detected_city = t
        if t in STATE_CODE_MAP:
            detected_state = STATE_CODE_MAP[t]
        elif t in STATE_CODE_MAP.values():
            detected_state = t

    normalized_text = " ".join(expanded_tokens)
    return {
        "original": raw,
        "normalized_text": normalized_text,
        "tokens": expanded_tokens,
        "pin_code": pin_code,
        "city": detected_city,
        "state": detected_state,
    }


def compute_string_similarity(s1: str, s2: str) -> float:
    """Compute token-based Jaccard similarity and SequenceMatcher ratio."""
    if not s1 or not s2:
        return 0.0
    if s1 == s2:
        return 1.0

    import difflib
    seq_ratio = difflib.SequenceMatcher(None, s1, s2).ratio()
    t1, t2 = set(s1.split()), set(s2.split())
    jaccard = len(t1 & t2) / len(t1 | t2) if (t1 or t2) else 0.0

    # Character bigram Dice similarity
    bg1 = {s1[i:i+2] for i in range(len(s1)-1)}
    bg2 = {s2[i:i+2] for i in range(len(s2)-1)}
    dice = (2.0 * len(bg1 & bg2)) / (len(bg1) + len(bg2)) if (bg1 or bg2) else 0.0

    return max(seq_ratio, jaccard, dice)


# ── Date Parsing Helpers ─────────────────────────────────────────────────────

DATE_PATTERNS = [
    r"\b(\d{4})-(\d{2})-(\d{2})\b",           # YYYY-MM-DD
    r"\b(\d{2})[/-](\d{2})[/-](\d{4})\b",     # DD/MM/YYYY or DD-MM-YYYY
    r"\b(\d{2})[/-](\d{2})[/-](\d{2})\b",       # DD/MM/YY
]

MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def parse_flexible_date(date_str: Any) -> date | None:
    """Parse flexible date strings commonly found in Indian loan documents."""
    if not date_str or not isinstance(date_str, str):
        return None
    raw = date_str.strip()

    # Try ISO YYYY-MM-DD
    m1 = re.search(r"\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b", raw)
    if m1:
        try:
            return date(int(m1.group(1)), int(m1.group(2)), int(m1.group(3)))
        except ValueError:
            pass

    # Try DD/MM/YYYY
    m2 = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", raw)
    if m2:
        try:
            return date(int(m2.group(3)), int(m2.group(2)), int(m2.group(1)))
        except ValueError:
            pass

    # Try "Month YYYY" or "DD Month YYYY"
    for mname, mnum in MONTH_NAMES.items():
        pattern = rf"\b(?:(\d{{1,2}})\s+)?{mname}\s+(\d{{4}})\b"
        m3 = re.search(pattern, raw, flags=re.IGNORECASE)
        if m3:
            day = int(m3.group(1)) if m3.group(1) else 1
            try:
                return date(int(m3.group(2)), mnum, day)
            except ValueError:
                pass

    return None


# ── Check Data Structure ─────────────────────────────────────────────────────

@dataclass
class CrossDocumentCheck:
    check_id: str
    check_type: str
    fields_compared: list[str]
    source_documents: list[str]
    observed_values: list[dict[str, Any]]
    match_status: str
    severity: str
    explanation: str
    requires_manual_review: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "check_type": self.check_type,
            "fields_compared": self.fields_compared,
            "source_documents": self.source_documents,
            "observed_values": mask_sensitive_value(self.observed_values),
            "match_status": self.match_status,
            "severity": self.severity,
            "explanation": mask_sensitive_value(self.explanation),
            "requires_manual_review": self.requires_manual_review,
        }


# ── Verification Check Engines ───────────────────────────────────────────────

class CrossDocumentVerifier:
    """
    Dedicated cross-document verification engine.
    Compares application inputs, extracted fields, and document metadata deterministically.
    """

    def __init__(self, config: CrossDocConfig | None = None) -> None:
        self.config = config or CrossDocConfig()
        self._check_counter = 0

    def _next_id(self, prefix: str) -> str:
        self._check_counter += 1
        return f"CHK_{prefix}_{self._check_counter:03d}"

    # ── 1. Identity Consistency ───────────────────────────────────────────────

    def compare_identity(
        self,
        app_name: str | None,
        doc_names: list[dict[str, Any]],
    ) -> list[CrossDocumentCheck]:
        """
        Compare borrower names across application declarations and uploaded documents.
        Handles initials, honorifics, token permutations, and phonetics.
        """
        checks: list[CrossDocumentCheck] = []
        if not app_name and not doc_names:
            return checks

        norm_app, app_tokens = normalize_name(app_name or "")
        app_token_set = set(app_tokens)

        if not doc_names:
            checks.append(CrossDocumentCheck(
                check_id=self._next_id("ID"),
                check_type="identity_consistency",
                fields_compared=["applicant_name"],
                source_documents=["application_details"],
                observed_values=[{
                    "source": "application_details",
                    "value": app_name,
                    "page_number": None,
                }],
                match_status="inconclusive",
                severity="info",
                explanation="Applicant name declared in application, but no identity fields were extracted from documents for cross-comparison.",
                requires_manual_review=False,
            ))
            return checks

        for item in doc_names:
            doc_name = item.get("value")
            doc_source = item.get("source_document") or item.get("filename") or "document"
            page_no = item.get("page_number")
            field_name = item.get("field_name") or "name"

            if not doc_name:
                checks.append(CrossDocumentCheck(
                    check_id=self._next_id("ID"),
                    check_type="identity_consistency",
                    fields_compared=["applicant_name", field_name],
                    source_documents=["application_details", doc_source],
                    observed_values=[
                        {"source": "application_details", "value": app_name, "page_number": None},
                        {"source": doc_source, "value": None, "page_number": page_no},
                    ],
                    match_status="missing_field",
                    severity="low",
                    explanation=f"Name field '{field_name}' not identified in document '{doc_source}'.",
                    requires_manual_review=False,
                ))
                continue

            norm_doc, doc_tokens = normalize_name(str(doc_name))
            doc_token_set = set(doc_tokens)

            # Exact match (original or normalized)
            if app_name and str(app_name).strip().lower() == str(doc_name).strip().lower():
                status = "exact_match"
                sev = "info"
                expl = f"Exact case-insensitive identity match observed between application and '{doc_source}'."
                review = False
            elif norm_app == norm_doc:
                status = "normalized_match"
                sev = "info"
                expl = f"Identity matches after removing honorifics and standardizing punctuation ('{app_name}' vs '{doc_name}')."
                review = False
            elif app_token_set == doc_token_set and len(app_tokens) > 1:
                # Name order variation (e.g. "Sharma Arjun" vs "Arjun Sharma")
                status = "normalized_match"
                sev = "info"
                expl = f"Name tokens match with word-order variation between application and '{doc_source}' ('{app_name}' vs '{doc_name}')."
                review = False
            elif self._is_initials_match(app_tokens, doc_tokens):
                # Initials match (e.g. "A. K. Sharma" vs "Arjun Kumar Sharma")
                status = "partial_match"
                sev = "low"
                expl = f"Name matches with abbreviated initial(s) between application and '{doc_source}' ('{app_name}' vs '{doc_name}')."
                review = False
            elif self._is_middle_name_omission(app_tokens, doc_tokens):
                # Middle name omitted (e.g. "Arjun Sharma" vs "Arjun Kumar Sharma")
                status = "partial_match"
                sev = "low"
                expl = f"Name matches with middle name omitted in one record ('{app_name}' vs '{doc_name}')."
                review = False
            else:
                sim = compute_string_similarity(norm_app, norm_doc)
                if sim >= self.config.name_fuzzy_threshold:
                    status = "ambiguous_match"
                    sev = "medium"
                    expl = (
                        f"Phonetic or typographical variance detected between declared name '{app_name}' "
                        f"and documented name '{doc_name}' in '{doc_source}' (similarity: {sim:.2f}). "
                        f"Automated similarity does not prove identity; manual underwriter review required."
                    )
                    review = True
                elif sim >= self.config.name_partial_threshold:
                    status = "partial_match"
                    sev = "medium"
                    expl = (
                        f"Partial name overlap observed between '{app_name}' and '{doc_name}' in '{doc_source}'. "
                        f"Manual underwriter verification required."
                    )
                    review = True
                else:
                    status = "potential_mismatch"
                    sev = "high"
                    expl = (
                        f"Distinct names observed: declared '{app_name}' vs documented '{doc_name}' in '{doc_source}'. "
                        f"Identity inconsistency requires manual review."
                    )
                    review = True

            checks.append(CrossDocumentCheck(
                check_id=self._next_id("ID"),
                check_type="identity_consistency",
                fields_compared=["applicant_name", field_name],
                source_documents=["application_details", doc_source],
                observed_values=[
                    {"source": "application_details", "value": app_name, "page_number": None},
                    {"source": doc_source, "value": doc_name, "page_number": page_no},
                ],
                match_status=status,
                severity=sev,
                explanation=expl,
                requires_manual_review=review,
            ))

        return checks

    def _is_initials_match(self, tokens_a: list[str], tokens_b: list[str]) -> bool:
        """Check if one token list represents an abbreviated initial version of the other."""
        if not tokens_a or not tokens_b or len(tokens_a) != len(tokens_b):
            return False
        has_initial = False
        for a, b in zip(tokens_a, tokens_b):
            if a == b:
                continue
            if len(a) == 1 and b.startswith(a):
                has_initial = True
                continue
            if len(b) == 1 and a.startswith(b):
                has_initial = True
                continue
            return False
        return has_initial

    def _is_middle_name_omission(self, tokens_a: list[str], tokens_b: list[str]) -> bool:
        """Check if a 2-word name matches the first and last words of a 3-word name."""
        short, long = (tokens_a, tokens_b) if len(tokens_a) < len(tokens_b) else (tokens_b, tokens_a)
        if len(short) == 2 and len(long) == 3:
            return short[0] == long[0] and short[-1] == long[-1]
        return False

    # ── 2. Address Consistency ────────────────────────────────────────────────

    def compare_addresses(
        self,
        app_addresses: dict[str, str | None],
        doc_addresses: list[dict[str, Any]],
    ) -> list[CrossDocumentCheck]:
        """
        Compare address components across documents and application records.
        Distinguishes legitimate current vs permanent differences, PIN code mismatches,
        and includes strict disclaimers against physical geocoding claims.
        """
        checks: list[CrossDocumentCheck] = []
        if not app_addresses and not doc_addresses:
            return checks

        app_curr = app_addresses.get("current_address") or app_addresses.get("address")
        app_perm = app_addresses.get("permanent_address")

        norm_curr = normalize_address(app_curr or "")
        norm_perm = normalize_address(app_perm or "")

        # Check declared current vs declared permanent address
        if app_curr and app_perm and app_curr.strip().lower() != app_perm.strip().lower():
            if self.config.allow_current_permanent_difference:
                checks.append(CrossDocumentCheck(
                    check_id=self._next_id("ADDR"),
                    check_type="address_consistency",
                    fields_compared=["current_address", "permanent_address"],
                    source_documents=["application_details"],
                    observed_values=[
                        {"source": "application_details (current)", "value": app_curr, "page_number": None},
                        {"source": "application_details (permanent)", "value": app_perm, "page_number": None},
                    ],
                    match_status="legitimate_difference",
                    severity="info",
                    explanation=(
                        "Declared current residential address differs from permanent address. "
                        "This is permissible under retail lending guidelines; no address conflict flagged. "
                        "(Note: No physical postal inspection or geocoding performed)."
                    ),
                    requires_manual_review=False,
                ))

        # Compare document addresses
        for item in doc_addresses:
            doc_addr = item.get("value")
            doc_source = item.get("source_document") or item.get("filename") or "document"
            page_no = item.get("page_number")
            addr_type = str(item.get("field_name") or "address").lower()

            if not doc_addr:
                checks.append(CrossDocumentCheck(
                    check_id=self._next_id("ADDR"),
                    check_type="address_consistency",
                    fields_compared=["address", addr_type],
                    source_documents=["application_details", doc_source],
                    observed_values=[
                        {"source": "application_details", "value": app_curr or app_perm, "page_number": None},
                        {"source": doc_source, "value": None, "page_number": page_no},
                    ],
                    match_status="missing_address",
                    severity="low",
                    explanation=f"Address field not present in document '{doc_source}'.",
                    requires_manual_review=False,
                ))
                continue

            norm_doc = normalize_address(str(doc_addr))

            # Select appropriate comparison target (perm vs curr)
            is_perm_doc = "permanent" in addr_type or any(k in doc_source.lower() for k in ["aadhaar", "passport", "voter"])
            target_norm = norm_perm if (is_perm_doc and app_perm) else norm_curr
            target_label = "permanent_address" if (is_perm_doc and app_perm) else "current_address"
            raw_target = app_perm if (is_perm_doc and app_perm) else app_curr

            if not raw_target:
                checks.append(CrossDocumentCheck(
                    check_id=self._next_id("ADDR"),
                    check_type="address_consistency",
                    fields_compared=[target_label, addr_type],
                    source_documents=["application_details", doc_source],
                    observed_values=[
                        {"source": "application_details", "value": None, "page_number": None},
                        {"source": doc_source, "value": doc_addr, "page_number": page_no},
                    ],
                    match_status="inconclusive",
                    severity="info",
                    explanation=f"Document '{doc_source}' provides an address, but no corresponding application address was declared.",
                    requires_manual_review=False,
                ))
                continue

            # Compare PIN codes if present
            pin_target = target_norm.get("pin_code")
            pin_doc = norm_doc.get("pin_code")

            if pin_target and pin_doc and pin_target != pin_doc:
                # Check if this matches the OTHER declared address (e.g. Aadhaar matches permanent while utility matches current)
                other_norm = norm_curr if target_norm == norm_perm else norm_perm
                if other_norm.get("pin_code") == pin_doc and self.config.allow_current_permanent_difference:
                    status = "legitimate_difference"
                    sev = "info"
                    expl = (
                        f"Document '{doc_source}' PIN code ({pin_doc}) aligns with the applicant's other declared address. "
                        f"Standard residence variance noted. (No physical geocoding performed)."
                    )
                    review = False
                else:
                    status = "potential_mismatch"
                    sev = "high"
                    expl = (
                        f"Postal PIN code mismatch detected: declared {target_label} has PIN '{pin_target}', "
                        f"whereas '{doc_source}' has PIN '{pin_doc}'. Manual verification required."
                    )
                    review = True
            elif target_norm["normalized_text"] == norm_doc["normalized_text"]:
                status = "normalized_match"
                sev = "info"
                expl = (
                    f"Address matches normalized components between application and '{doc_source}'. "
                    f"(Note: Address not physically verified or geocoded)."
                )
                review = False
            else:
                # Token overlap comparison
                t_target = set(target_norm["tokens"])
                t_doc = set(norm_doc["tokens"])
                overlap = len(t_target & t_doc) / max(len(t_target), 1)

                if overlap >= self.config.address_token_overlap_threshold:
                    status = "normalized_match"
                    sev = "info"
                    expl = (
                        f"Address matches on key locality, street, and postal tokens between application and '{doc_source}' "
                        f"({overlap*100:.0f}% overlap). (Note: Address not physically verified or geocoded)."
                    )
                    review = False
                elif overlap >= self.config.address_partial_overlap_threshold:
                    status = "partial_match"
                    sev = "low"
                    expl = (
                        f"Partial address match observed ({overlap*100:.0f}% component overlap) between application and '{doc_source}'. "
                        f"Minor formatting or building phrasing variances present."
                    )
                    review = False
                elif len(target_norm["tokens"]) <= 2 or len(norm_doc["tokens"]) <= 2:
                    status = "inconclusive"
                    sev = "info"
                    expl = (
                        f"Inconclusive address comparison: insufficient address components extracted from '{doc_source}' "
                        f"to perform confident validation."
                    )
                    review = False
                else:
                    status = "potential_mismatch"
                    sev = "medium"
                    expl = (
                        f"Substantial phrasing discrepancy between declared address and '{doc_source}'. "
                        f"Requires human underwriter review."
                    )
                    review = True

            checks.append(CrossDocumentCheck(
                check_id=self._next_id("ADDR"),
                check_type="address_consistency",
                fields_compared=[target_label, addr_type],
                source_documents=["application_details", doc_source],
                observed_values=[
                    {"source": f"application_details ({target_label})", "value": raw_target, "page_number": None},
                    {"source": doc_source, "value": doc_addr, "page_number": page_no},
                ],
                match_status=status,
                severity=sev,
                explanation=expl,
                requires_manual_review=review,
            ))

        return checks

    # ── 3. Date & Employment Consistency ──────────────────────────────────────

    def compare_dates_and_employment(
        self,
        app_details: dict[str, Any],
        doc_employers: list[dict[str, Any]],
        doc_dates: list[dict[str, Any]],
    ) -> list[CrossDocumentCheck]:
        """
        Compare employer names, document recency, and expiry dates.
        Does not assume universal document expiry; uses configurable recency windows.
        """
        checks: list[CrossDocumentCheck] = []
        app_employer = app_details.get("employer_name") or app_details.get("employer")
        app_emp_type = app_details.get("employment_type")
        ref_date = parse_flexible_date(app_details.get("application_date")) or date.today()

        norm_app_emp, app_emp_tokens = normalize_company_name(app_employer or "")

        # 3.1 Employer Name Comparison
        for item in doc_employers:
            doc_emp = item.get("value")
            doc_source = item.get("source_document") or item.get("filename") or "document"
            page_no = item.get("page_number")

            if not doc_emp:
                continue

            norm_doc_emp, doc_emp_tokens = normalize_company_name(str(doc_emp))

            if not app_employer:
                checks.append(CrossDocumentCheck(
                    check_id=self._next_id("EMP"),
                    check_type="employment_consistency",
                    fields_compared=["employer_name"],
                    source_documents=["application_details", doc_source],
                    observed_values=[
                        {"source": "application_details", "value": None, "page_number": None},
                        {"source": doc_source, "value": doc_emp, "page_number": page_no},
                    ],
                    match_status="inconclusive",
                    severity="info",
                    explanation=f"Employer '{doc_emp}' extracted from '{doc_source}', but no employer was declared in the application.",
                    requires_manual_review=False,
                ))
                continue

            if norm_app_emp == norm_doc_emp:
                status = "normalized_match"
                sev = "info"
                expl = f"Employer name matches normalized corporate entities between application and '{doc_source}' ('{app_employer}' vs '{doc_emp}')."
                review = False
            elif set(app_emp_tokens) == set(doc_emp_tokens) and app_emp_tokens:
                status = "normalized_match"
                sev = "info"
                expl = f"Employer name matches with word-order variation ('{app_employer}' vs '{doc_emp}')."
                review = False
            elif self._is_corporate_acronym(app_employer, str(doc_emp)):
                status = "partial_match"
                sev = "low"
                expl = f"Corporate acronym correspondence observed between '{app_employer}' and '{doc_emp}' in '{doc_source}'. Standard underwriter verification recommended."
                review = True
            else:
                sim = compute_string_similarity(norm_app_emp, norm_doc_emp)
                if sim >= 0.70:
                    status = "partial_match"
                    sev = "low"
                    expl = f"Close employer name correspondence ({sim*100:.0f}% similarity) between '{app_employer}' and '{doc_emp}' in '{doc_source}'."
                    review = False
                else:
                    status = "potential_mismatch"
                    sev = "high"
                    expl = f"Conflicting employer names: declared '{app_employer}' vs documented '{doc_emp}' in '{doc_source}'."
                    review = True

            checks.append(CrossDocumentCheck(
                check_id=self._next_id("EMP"),
                check_type="employment_consistency",
                fields_compared=["employer_name"],
                source_documents=["application_details", doc_source],
                observed_values=[
                    {"source": "application_details", "value": app_employer, "page_number": None},
                    {"source": doc_source, "value": doc_emp, "page_number": page_no},
                ],
                match_status=status,
                severity=sev,
                explanation=expl,
                requires_manual_review=review,
            ))

        # 3.2 Document Recency & Expiry Checks
        for item in doc_dates:
            raw_date = item.get("value")
            doc_source = item.get("source_document") or item.get("filename") or "document"
            doc_cat = item.get("document_category") or ""
            date_field = item.get("field_name") or "document_date"
            page_no = item.get("page_number")

            if not raw_date:
                continue

            parsed_d = parse_flexible_date(raw_date)
            if not parsed_d:
                checks.append(CrossDocumentCheck(
                    check_id=self._next_id("DATE"),
                    check_type="date_recency_consistency",
                    fields_compared=[date_field],
                    source_documents=[doc_source],
                    observed_values=[{"source": doc_source, "value": raw_date, "page_number": page_no}],
                    match_status="inconclusive",
                    severity="info",
                    explanation=f"Date string '{raw_date}' in '{doc_source}' could not be parsed unambiguously to evaluate recency.",
                    requires_manual_review=False,
                ))
                continue

            # Determine maximum allowable age
            max_age = self.config.salary_slip_max_age_days
            if "bank" in doc_cat.lower() or "bank" in doc_source.lower():
                max_age = self.config.bank_statement_max_age_days
            elif "itr" in doc_cat.lower() or "tax" in doc_cat.lower() or "income-tax" in doc_cat.lower():
                max_age = self.config.itr_max_age_days

            # Expiry date check
            if "expiry" in date_field.lower():
                if parsed_d < ref_date:
                    checks.append(CrossDocumentCheck(
                        check_id=self._next_id("DATE"),
                        check_type="date_recency_consistency",
                        fields_compared=[date_field],
                        source_documents=[doc_source],
                        observed_values=[{"source": doc_source, "value": str(parsed_d), "page_number": page_no}],
                        match_status="potential_mismatch",
                        severity="high",
                        explanation=f"Identity document in '{doc_source}' has expired (expiry date: {parsed_d}, reference date: {ref_date}).",
                        requires_manual_review=True,
                    ))
                else:
                    checks.append(CrossDocumentCheck(
                        check_id=self._next_id("DATE"),
                        check_type="date_recency_consistency",
                        fields_compared=[date_field],
                        source_documents=[doc_source],
                        observed_values=[{"source": doc_source, "value": str(parsed_d), "page_number": page_no}],
                        match_status="exact_match",
                        severity="info",
                        explanation=f"Document '{doc_source}' is currently valid (expires: {parsed_d}).",
                        requires_manual_review=False,
                    ))
                continue

            # Recency check
            age_days = (ref_date - parsed_d).days
            if age_days < 0:
                # Future date (allow up to 30 days for forward statement periods)
                if abs(age_days) > 30:
                    status = "potential_mismatch"
                    sev = "medium"
                    expl = f"Document date ({parsed_d}) is in the future relative to application reference date ({ref_date})."
                    review = True
                else:
                    status = "exact_match"
                    sev = "info"
                    expl = f"Document date ({parsed_d}) is concurrent with application submission."
                    review = False
            elif age_days > max_age:
                status = "potential_mismatch"
                sev = "medium"
                expl = (
                    f"Document '{doc_source}' date ({parsed_d}) is {age_days} days old, "
                    f"exceeding the configured {max_age}-day recency requirement."
                )
                review = True
            else:
                status = "normalized_match"
                sev = "info"
                expl = f"Document '{doc_source}' date ({parsed_d}) satisfies the {max_age}-day validity requirement ({age_days} days old)."
                review = False

            checks.append(CrossDocumentCheck(
                check_id=self._next_id("DATE"),
                check_type="date_recency_consistency",
                fields_compared=[date_field],
                source_documents=[doc_source],
                observed_values=[{"source": doc_source, "value": str(parsed_d), "page_number": page_no}],
                match_status=status,
                severity=sev,
                explanation=expl,
                requires_manual_review=review,
            ))

        return checks

    def _is_corporate_acronym(self, name_a: str, name_b: str) -> bool:
        """Check if one name is an uppercase acronym of the words in the other name."""
        tokens_a = [t for t in normalize_tokens(name_a) if t not in HONORIFICS]
        tokens_b = [t for t in normalize_tokens(name_b) if t not in HONORIFICS]
        acronym_a = "".join(t[0] for t in tokens_a if t).upper()
        acronym_b = "".join(t[0] for t in tokens_b if t).upper()
        clean_a = name_a.strip().upper()
        clean_b = name_b.strip().upper()
        return clean_a == acronym_b or clean_b == acronym_a

    # ── 4. Financial Consistency ──────────────────────────────────────────────

    def compare_financials(
        self,
        app_income: float | int | None,
        salary_slip_figures: dict[str, Any] | None,
        bank_statement_figures: dict[str, Any] | None,
    ) -> list[CrossDocumentCheck]:
        """
        Compare declared income against salary slip gross/net figures and bank statement credits.
        Clarifies standard statutory deductions and avoids assuming all bank deposits are recurring salary.
        """
        checks: list[CrossDocumentCheck] = []
        try:
            declared_num = float(app_income) if app_income is not None else None
        except (ValueError, TypeError):
            declared_num = None

        gross_num = self._safe_float(salary_slip_figures.get("gross_salary") if salary_slip_figures else None)
        net_num = self._safe_float(salary_slip_figures.get("net_salary") if salary_slip_figures else None)
        salary_doc = salary_slip_figures.get("source_document", "Salary slip") if salary_slip_figures else "Salary slip"

        bank_credit_num = self._safe_float(bank_statement_figures.get("salary_credit_amount") if bank_statement_figures else None)
        bank_doc = bank_statement_figures.get("source_document", "Bank statement") if bank_statement_figures else "Bank statement"

        # 4.1 Declared Income vs Salary Slip Figures
        if declared_num is not None and (gross_num is not None or net_num is not None):
            obs = [{"source": "application_details", "value": f"INR {declared_num:,.2f}", "page_number": None}]
            if gross_num is not None:
                obs.append({"source": salary_doc, "value": f"Gross: INR {gross_num:,.2f}", "page_number": salary_slip_figures.get("page_number")})
            if net_num is not None:
                obs.append({"source": salary_doc, "value": f"Net: INR {net_num:,.2f}", "page_number": salary_slip_figures.get("page_number")})

            # Check if declared matches gross within tolerance
            gross_match = gross_num and abs(declared_num - gross_num) / gross_num <= (self.config.salary_variance_tolerance_pct / 100.0)
            net_match = net_num and abs(declared_num - net_num) / net_num <= (self.config.salary_variance_tolerance_pct / 100.0)

            if gross_match:
                status = "normalized_match"
                sev = "info"
                expl = (
                    f"Declared monthly income (INR {declared_num:,.2f}) closely corresponds with documented "
                    f"Gross Salary (INR {gross_num:,.2f}) on '{salary_doc}'."
                )
                review = False
            elif net_match:
                status = "normalized_match"
                sev = "info"
                expl = (
                    f"Declared monthly income (INR {declared_num:,.2f}) closely corresponds with documented "
                    f"Net Take-Home Salary (INR {net_num:,.2f}) on '{salary_doc}'."
                )
                review = False
            elif gross_num and declared_num > gross_num:
                var_pct = ((declared_num - gross_num) / gross_num) * 100.0
                if var_pct <= self.config.large_salary_variance_pct:
                    status = "potential_mismatch"
                    sev = "medium"
                    expl = (
                        f"Declared income (INR {declared_num:,.2f}) exceeds documented gross salary "
                        f"(INR {gross_num:,.2f}) by {var_pct:.1f}%. Underwriter verification of supplementary income required."
                    )
                    review = True
                else:
                    status = "potential_mismatch"
                    sev = "high"
                    expl = (
                        f"Significant variance: declared income (INR {declared_num:,.2f}) exceeds documented "
                        f"gross salary (INR {gross_num:,.2f}) by {var_pct:.1f}%."
                    )
                    review = True
            elif gross_num and declared_num < gross_num:
                status = "normalized_match"
                sev = "info"
                expl = (
                    f"Declared monthly income (INR {declared_num:,.2f}) is conservative and fully supported "
                    f"by documented gross salary (INR {gross_num:,.2f})."
                )
                review = False
            else:
                status = "inconclusive"
                sev = "info"
                expl = "Declared income and documented salary cannot be reconciled without additional pay components."
                review = False

            checks.append(CrossDocumentCheck(
                check_id=self._next_id("FIN"),
                check_type="financial_consistency",
                fields_compared=["monthly_income", "salary"],
                source_documents=["application_details", salary_doc],
                observed_values=obs,
                match_status=status,
                severity=sev,
                explanation=expl,
                requires_manual_review=review,
            ))

        # 4.2 Salary Slip Net vs Bank Credit Amount
        if net_num is not None and bank_credit_num is not None:
            tol = self.config.bank_salary_credit_tolerance_pct / 100.0
            diff = abs(net_num - bank_credit_num)
            pct_diff = (diff / net_num) * 100.0

            if pct_diff <= self.config.bank_salary_credit_tolerance_pct:
                status = "exact_match"
                sev = "info"
                expl = (
                    f"Salary slip net pay (INR {net_num:,.2f}) matches bank statement salary credit "
                    f"(INR {bank_credit_num:,.2f}) within {pct_diff:.1f}% tolerance."
                )
                review = False
            else:
                status = "partial_match"
                sev = "medium"
                expl = (
                    f"Variance between salary slip net pay (INR {net_num:,.2f}) and bank deposit credit "
                    f"(INR {bank_credit_num:,.2f}) is {pct_diff:.1f}%. "
                    f"(Note: Not all bank deposits represent recurring salary; manual credit verification recommended)."
                )
                review = True

            checks.append(CrossDocumentCheck(
                check_id=self._next_id("FIN"),
                check_type="financial_consistency",
                fields_compared=["net_salary", "bank_salary_credit"],
                source_documents=[salary_doc, bank_doc],
                observed_values=[
                    {"source": salary_doc, "value": f"Net Salary: INR {net_num:,.2f}", "page_number": salary_slip_figures.get("page_number")},
                    {"source": bank_doc, "value": f"Bank Credit: INR {bank_credit_num:,.2f}", "page_number": bank_statement_figures.get("page_number")},
                ],
                match_status=status,
                severity=sev,
                explanation=expl,
                requires_manual_review=review,
            ))

        # 4.3 Bank Account Holder Name Verification
        acct_holder = bank_statement_figures.get("account_holder_name") if bank_statement_figures else None
        app_name = salary_slip_figures.get("applicant_name") if salary_slip_figures else None

        if acct_holder and app_name:
            norm_acct, acct_tokens = normalize_name(acct_holder)
            norm_app, app_tokens = normalize_name(app_name)

            if norm_acct == norm_app or set(acct_tokens) == set(app_tokens):
                status = "normalized_match"
                sev = "info"
                expl = f"Bank statement account holder ('{acct_holder}') matches applicant name."
                review = False
            elif self._is_initials_match(app_tokens, acct_tokens):
                status = "partial_match"
                sev = "low"
                expl = f"Bank statement account holder ('{acct_holder}') matches applicant name with initial abbreviation."
                review = False
            else:
                sim = compute_string_similarity(norm_acct, norm_app)
                if sim >= 0.80:
                    status = "ambiguous_match"
                    sev = "medium"
                    expl = f"Slight phonetic/spelling variance between bank account holder ('{acct_holder}') and applicant name ('{app_name}')."
                    review = True
                else:
                    status = "potential_mismatch"
                    sev = "high"
                    expl = f"Bank account holder name ('{acct_holder}') differs from applicant name ('{app_name}'). Third-party account review required."
                    review = True

            checks.append(CrossDocumentCheck(
                check_id=self._next_id("FIN"),
                check_type="financial_consistency",
                fields_compared=["applicant_name", "account_holder_name"],
                source_documents=["application_details", bank_doc],
                observed_values=[
                    {"source": "application_details", "value": app_name, "page_number": None},
                    {"source": bank_doc, "value": acct_holder, "page_number": bank_statement_figures.get("page_number")},
                ],
                match_status=status,
                severity=sev,
                explanation=expl,
                requires_manual_review=review,
            ))

        return checks

    def _safe_float(self, val: Any) -> float | None:
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            clean = re.sub(r"[^\d.-]", "", val)
            try:
                return float(clean)
            except ValueError:
                return None
        return None

    # ── 5. Duplicate & Contradictory Document Checks ──────────────────────────

    def detect_duplicates_and_contradictions(
        self,
        documents: list[dict[str, Any]],
        document_bytes_map: dict[str, bytes] | None = None,
    ) -> list[CrossDocumentCheck]:
        """
        Detect repeated uploads via SHA-256 file hashing, and flag contradictory statement periods.
        Never silently discards documents.
        """
        checks: list[CrossDocumentCheck] = []
        if not documents or len(documents) < 2:
            return checks

        # 5.1 SHA-256 Hash Duplicate Check
        hashes: dict[str, list[str]] = {}
        bytes_map = document_bytes_map or {}

        for doc in documents:
            fname = str(doc.get("filename") or "")
            raw_bytes = bytes_map.get(fname)

            if raw_bytes:
                h = hashlib.sha256(raw_bytes).hexdigest()
            else:
                # Fallback to extracted text hash if raw bytes not passed
                text_content = str(doc.get("text") or "").strip()
                if len(text_content) > 50:
                    h = hashlib.sha256(text_content.encode("utf-8")).hexdigest()
                else:
                    h = None

            if h:
                hashes.setdefault(h, []).append(fname)

        for h, fnames in hashes.items():
            if len(fnames) > 1:
                checks.append(CrossDocumentCheck(
                    check_id=self._next_id("DUP"),
                    check_type="duplicate_document_check",
                    fields_compared=["file_content_hash"],
                    source_documents=fnames,
                    observed_values=[
                        {"source": fn, "value": f"SHA-256:{h[:12]}...", "page_number": None}
                        for fn in fnames
                    ],
                    match_status="duplicate_detected",
                    severity="medium",
                    explanation=(
                        f"Identical files uploaded: {', '.join(fnames)} share matching cryptographic "
                        f"SHA-256 hash ({h[:12]}...). Documents retained for evidentiary completeness."
                    ),
                    requires_manual_review=True,
                ))

        # 5.2 Contradictory Statement Periods
        periods: list[dict[str, Any]] = []
        for doc in documents:
            fname = doc.get("filename") or ""
            period = doc.get("statement_period") or doc.get("period")
            if period:
                periods.append({"filename": fname, "period": str(period), "doc": doc})

        for i in range(len(periods)):
            for j in range(i + 1, len(periods)):
                p1, p2 = periods[i], periods[j]
                if p1["period"] == p2["period"] and p1["filename"] != p2["filename"]:
                    # Check if balances or figures conflict
                    checks.append(CrossDocumentCheck(
                        check_id=self._next_id("CONTRA"),
                        check_type="duplicate_document_check",
                        fields_compared=["statement_period"],
                        source_documents=[p1["filename"], p2["filename"]],
                        observed_values=[
                            {"source": p1["filename"], "value": p1["period"], "page_number": None},
                            {"source": p2["filename"], "value": p2["period"], "page_number": None},
                        ],
                        match_status="overlapping_period_conflict",
                        severity="medium",
                        explanation=(
                            f"Uploaded documents '{p1['filename']}' and '{p2['filename']}' cover the exact "
                            f"same statement period ('{p1['period']}'). Review required to verify whether this is an "
                            f"accidental re-upload or contradictory accounting report."
                        ),
                        requires_manual_review=True,
                    ))

        return checks

    def compare_identifiers(
        self,
        app_details: dict[str, Any],
        doc_identifiers: list[dict[str, Any]],
    ) -> list[CrossDocumentCheck]:
        """
        Compare identity numbers (PAN, Aadhaar, Passport) across documents and declarations.
        All observed values are masked to protect PII.
        """
        checks: list[CrossDocumentCheck] = []
        if not doc_identifiers:
            return checks

        for item in doc_identifiers:
            val = item.get("value")
            source = item.get("source_document") or item.get("filename") or "document"
            fld = item.get("field_name") or "identity_number"
            page_no = item.get("page_number")

            if not val:
                continue

            app_val = app_details.get(fld) or app_details.get(fld.replace("_number", ""))
            if app_val:
                norm_app = re.sub(r"[\s-]", "", str(app_val)).upper()
                norm_doc = re.sub(r"[\s-]", "", str(val)).upper()
                if norm_app == norm_doc:
                    status = "exact_match"
                    sev = "info"
                    expl = f"Identifier '{fld}' matches between application and '{source}'."
                    review = False
                else:
                    status = "potential_mismatch"
                    sev = "critical"
                    expl = f"Identifier mismatch on '{fld}' between application and '{source}'."
                    review = True

                checks.append(CrossDocumentCheck(
                    check_id=self._next_id("ID_NUM"),
                    check_type="identity_consistency",
                    fields_compared=[fld],
                    source_documents=["application_details", source],
                    observed_values=[
                        {"source": "application_details", "value": mask_sensitive_value(str(app_val)), "page_number": None},
                        {"source": source, "value": mask_sensitive_value(str(val)), "page_number": page_no},
                    ],
                    match_status=status,
                    severity=sev,
                    explanation=expl,
                    requires_manual_review=review,
                ))
            else:
                checks.append(CrossDocumentCheck(
                    check_id=self._next_id("ID_NUM"),
                    check_type="identity_consistency",
                    fields_compared=[fld],
                    source_documents=[source],
                    observed_values=[
                        {"source": source, "value": mask_sensitive_value(str(val)), "page_number": page_no},
                    ],
                    match_status="exact_match",
                    severity="info",
                    explanation=f"Identifier '{fld}' extracted from '{source}' and stored with sensitive masking.",
                    requires_manual_review=False,
                ))
        return checks

    # ── Master Verification Orchestrator ──────────────────────────────────────

    def verify_application(
        self,
        application_details: dict[str, Any],
        extracted_fields: list[dict[str, Any]],
        documents: list[dict[str, Any]],
        lender_context: dict[str, Any] | None = None,
        document_bytes_map: dict[str, bytes] | None = None,
    ) -> dict[str, Any]:
        """
        Run all cross-document verification checks and synthesize a consolidated report.
        """
        all_checks: list[CrossDocumentCheck] = []

        # Categorize extracted fields
        doc_names: list[dict[str, Any]] = []
        doc_identifiers: list[dict[str, Any]] = []
        doc_addresses: list[dict[str, Any]] = []
        doc_employers: list[dict[str, Any]] = []
        doc_dates: list[dict[str, Any]] = []
        salary_slip_figs: dict[str, Any] = {}
        bank_figs: dict[str, Any] = {}

        for fld in extracted_fields:
            if not isinstance(fld, dict):
                continue
            fname = str(fld.get("field_name") or "").lower()
            val = fld.get("observed_value")
            source = fld.get("source_document") or fld.get("source_filename")
            page_no = fld.get("page_number")

            if any(k in fname for k in ["pan", "aadhaar", "uid", "passport", "voter"]):
                doc_identifiers.append({"field_name": fname, "value": val, "source_document": source, "page_number": page_no})
            elif "name" in fname and "employer" not in fname and "company" not in fname:
                doc_names.append({"field_name": fname, "value": val, "source_document": source, "page_number": page_no})
            elif "address" in fname:
                doc_addresses.append({"field_name": fname, "value": val, "source_document": source, "page_number": page_no})
            elif "employer" in fname or "company" in fname or "business" in fname:
                doc_employers.append({"field_name": fname, "value": val, "source_document": source, "page_number": page_no})
            elif "date" in fname or "period" in fname or "expiry" in fname:
                doc_dates.append({"field_name": fname, "value": val, "source_document": source, "page_number": page_no})

            # Financial aggregations
            if "gross" in fname and "salary" in fname:
                salary_slip_figs["gross_salary"] = val
                salary_slip_figs["source_document"] = source
                salary_slip_figs["page_number"] = page_no
            elif "net" in fname and "salary" in fname:
                salary_slip_figs["net_salary"] = val
                salary_slip_figs["source_document"] = source
                salary_slip_figs["page_number"] = page_no
            elif "credit" in fname and ("salary" in fname or "deposit" in fname):
                bank_figs["salary_credit_amount"] = val
                bank_figs["source_document"] = source
                bank_figs["page_number"] = page_no
            elif "account_holder" in fname or ("holder" in fname and "name" in fname):
                bank_figs["account_holder_name"] = val
                bank_figs["source_document"] = source
                bank_figs["page_number"] = page_no

        app_name = application_details.get("applicant_name") or application_details.get("full_name")
        salary_slip_figs["applicant_name"] = app_name

        # Address mapping
        app_addresses = {
            "current_address": application_details.get("current_address") or application_details.get("address"),
            "permanent_address": application_details.get("permanent_address"),
        }

        # 1. Identity Consistency Checks (Names & Identifiers)
        all_checks.extend(self.compare_identity(app_name, doc_names))
        all_checks.extend(self.compare_identifiers(application_details, doc_identifiers))

        # 2. Address Consistency Checks
        all_checks.extend(self.compare_addresses(app_addresses, doc_addresses))

        # 3. Date & Employment Consistency Checks
        all_checks.extend(self.compare_dates_and_employment(application_details, doc_employers, doc_dates))

        # 4. Financial Consistency Checks
        app_income = application_details.get("monthly_income") or application_details.get("declared_income")
        all_checks.extend(self.compare_financials(app_income, salary_slip_figs, bank_figs))

        # 5. Duplicate and Contradictory Document Checks
        all_checks.extend(self.detect_duplicates_and_contradictions(documents, document_bytes_map))

        # Compute summary
        total = len(all_checks)
        passed = sum(1 for c in all_checks if c.match_status in {"exact_match", "normalized_match", "legitimate_difference"})
        partial = sum(1 for c in all_checks if c.match_status == "partial_match")
        mismatches = sum(1 for c in all_checks if c.match_status in {"potential_mismatch", "overlapping_period_conflict"})
        inconclusive = sum(1 for c in all_checks if c.match_status in {"inconclusive", "missing_field", "missing_address"})
        duplicates = sum(1 for c in all_checks if c.match_status == "duplicate_detected")
        review_required = sum(1 for c in all_checks if c.requires_manual_review)

        if mismatches > 0:
            overall_status = "inconsistent"
        elif review_required > 0:
            overall_status = "review_required"
        elif inconclusive > 0 and passed == 0:
            overall_status = "inconclusive"
        else:
            overall_status = "consistent"

        disclaimers = [
            "Deterministic cross-document comparisons normalize formatting, abbreviations, and name ordering but do not guarantee external cryptographic identity verification.",
            "Postal address comparison identifies component alignment only and does not verify physical delivery or geocoding without an external postal verification API.",
            "Legitimate differences between current and permanent addresses are recognized under standard lending criteria.",
            "Bank deposit credits are analyzed for correlation with salary slips but are not assumed to represent recurring employment income without underwriter confirmation.",
        ]

        return {
            "cross_document_summary": {
                "total_checks": total,
                "passed_checks": passed,
                "partial_matches": partial,
                "mismatch_checks": mismatches,
                "inconclusive_checks": inconclusive,
                "duplicate_checks": duplicates,
                "requires_manual_review_count": review_required,
                "overall_consistency_status": overall_status,
            },
            "cross_document_checks": [c.to_dict() for c in all_checks],
            "disclaimers": disclaimers,
        }


# ── Global Convenience API ───────────────────────────────────────────────────

def verify_cross_document_consistency(
    application_details: dict[str, Any],
    extracted_fields: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    lender_context: dict[str, Any] | None = None,
    document_bytes_map: dict[str, bytes] | None = None,
    config: CrossDocConfig | None = None,
) -> dict[str, Any]:
    """
    Main entry point for cross-document consistency verification.

    Args:
        application_details: Borrower application fields (name, address, employer, income, etc.).
        extracted_fields: List of structured extracted fields from all uploaded documents.
        documents: List of document metadata dictionaries (filename, text, statement_period).
        lender_context: Optional lender/product specifications (recency windows, checklist).
        document_bytes_map: Optional map of {filename: raw_bytes} for SHA-256 duplicate checks.
        config: Optional CrossDocConfig overrides.

    Returns:
        Consolidated report containing cross_document_summary, cross_document_checks, and disclaimers.
    """
    active_config = config or CrossDocConfig.from_lender_context(lender_context)
    verifier = CrossDocumentVerifier(active_config)
    return verifier.verify_application(
        application_details=application_details,
        extracted_fields=extracted_fields,
        documents=documents,
        lender_context=lender_context,
        document_bytes_map=document_bytes_map,
    )
