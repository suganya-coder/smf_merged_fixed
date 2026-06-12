




"""
auth_utils.py  —  Smart Attendance System v9.6 + E Auth Merge
==============================================================
Unified authentication utilities:
  - JWT via python-jose  (replaces PyJWT)
  - bcrypt password hashing via passlib
  - OTP generation + expiry  (uses secrets module — cryptographically secure)
  - Role guards (require_role)
  - get_current_user dependency for FastAPI

Drop-in replacement for the old inline JWT logic in api.py.
Import this module in api.py instead of using raw jwt calls.
==============================================================
"""

import os
import hmac
import hashlib
import logging
import secrets                      # FIX Bug-4: replaced random with secrets
import string
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────
# Use the same secret key as api.py/config.py so tokens signed at login
# are accepted by the role-attendance routes (auth_utils.get_current_user).
# Falling back to the env var and then the same stable default ensures
# tokens survive server restarts when API_SECRET_KEY is not set.
try:
    import config as _cfg
    SECRET_KEY = getattr(_cfg, "API_SECRET_KEY", None) or os.getenv("SECRET_KEY", "smf-super-secret-key-change-in-prod!")
except Exception:
    SECRET_KEY = os.getenv("SECRET_KEY", os.getenv("API_SECRET_KEY", "smf-super-secret-key-change-in-prod!"))

# Step 5 security fix: warn loudly if the secret key is a known-weak default.
_WEAK_KEYS = {
    "smf-super-secret-key-change-in-prod!",
    "smf-edutrack-super-secret-key-change-in-production-2024!",
    "changeme",
    "secret",
}
if SECRET_KEY in _WEAK_KEYS or len(SECRET_KEY) < 32:
    import warnings
    warnings.warn(
        "SECURITY: SECRET_KEY is weak or default — set a strong key in .env",
        RuntimeWarning,
        stacklevel=2,
    )

ALGORITHM      = os.getenv("ALGORITHM", "HS256")
# Step 5 security fix: was 480 min (8 hrs), now 60 min.
ACCESS_EXPIRE  = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))
OTP_EXPIRE_MIN = 10   # OTP valid for 10 minutes

# ── Password hashing ─────────────────────────────────────────
pwd_context   = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify plaintext against bcrypt hash."""
    return pwd_context.verify(plain, hashed)


# ── JWT ──────────────────────────────────────────────────────
def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """
    Create a signed JWT.
    data should include: {"sub": username, "role": role, "uid": user_id (optional)}
    """
    payload = data.copy()
    expire  = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_EXPIRE))
    # Step 5 security fix: include iat (issued-at) and jti (JWT ID) in every token.
    # jti prepares for token blocklist revocation in Step 8.
    # F-13: include tv (token version) so sessions are invalidated on pw change.
    # Import is deferred inside the function to avoid circular imports.
    try:
        from database import get_token_version as _get_tv
        _tv = _get_tv(data.get("sub", ""))
    except Exception:
        _tv = 0
    payload.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": secrets.token_hex(8),
        "tv":  _tv,
    })
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT.
    Raises HTTP 401 on any failure.
    """
    # Step 5 security fix: explicitly whitelist HS256 only — blocks alg="none"
    # and asymmetric-key confusion attacks.
    ALLOWED_ALGORITHMS = ["HS256"]   # never allow "none" or asymmetric confusion
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=ALLOWED_ALGORITHMS)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # F-11: check token blocklist — rejects tokens revoked via /auth/logout.
    # Import is deferred inside the function to avoid a circular import at
    # module load time (database.py imports config; auth_utils imports nothing
    # from the app layer at the top level).
    try:
        from database import is_token_blocked
        jti = payload.get("jti")
        if jti and is_token_blocked(jti):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked. Please log in again.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except HTTPException:
        raise
    except Exception as _bl_err:
        # Blocklist check failure must never silently pass a bad token —
        # log it and deny access until the DB is healthy again.
        log.error("decode_token: blocklist check failed — %s", _bl_err)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable.",
        )
    # F-13: check token version — rejects tokens issued before a password
    # reset/change.  A token whose 'tv' claim is less than the current version
    # in the DB belongs to a session that was invalidated by a pw change.
    try:
        from database import get_token_version as _get_tv
        tv         = payload.get("tv", 0)
        current_tv = _get_tv(payload.get("sub", ""))
        if tv < current_tv:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired. Please log in again.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except HTTPException:
        raise
    except Exception as _tv_err:
        log.error("decode_token: token-version check failed — %s", _tv_err)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable.",
        )
    return payload


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    """
    FastAPI dependency — extract and validate JWT from Authorization header.
    Works with both:
      - HTTPBearer scheme (Authorization: Bearer <token>)
      - Legacy header fallback (for backward compat with old frontend)
    """
    token = None

    # Try Bearer scheme first
    if credentials:
        token = credentials.credentials
    else:
        # Fallback: read raw header (legacy SMF frontend sends it this way)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No authentication token provided",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_token(token)


def require_role(*roles: str):
    """
    FastAPI dependency factory — enforce role membership.
    Usage: user: dict = Depends(require_role("admin", "hod"))
    """
    def checker(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {list(roles)}",
            )
        return user
    return checker


# ── Role-specific guards (convenience wrappers) ───────────────
def teacher_required(user: dict = Depends(get_current_user)) -> dict:
    """Allows: admin, hod, teacher, classincharge, faculty."""
    if user.get("role") not in ("admin", "hod", "teacher", "classincharge", "faculty"):
        raise HTTPException(status_code=403, detail="Teacher access required")
    return user


def admin_required(user: dict = Depends(get_current_user)) -> dict:
    """Allows: admin, hod."""
    if user.get("role") not in ("admin", "hod"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ── OTP ──────────────────────────────────────────────────────
# F-09: HMAC key for OTP hashing — derived from the same secret as JWTs
# so no extra env var is needed.  Encoded to bytes once at import time.
OTP_HMAC_KEY = os.getenv("SECRET_KEY", os.getenv("API_SECRET_KEY", "smf-secret")).encode()


def hash_otp(otp: str) -> str:
    """HMAC-SHA256 of the OTP — safe to store in DB.

    Produces a 64-char hex digest.  One-way: a DB dump reveals only the
    hash, not the 6-digit code the user must supply.
    """
    return hmac.new(OTP_HMAC_KEY, otp.encode(), hashlib.sha256).hexdigest()


def verify_otp_code(submitted: str, stored_hash: str) -> bool:
    """Constant-time comparison of submitted OTP against stored HMAC hash.

    Uses hmac.compare_digest to prevent timing-based side-channel attacks.
    Returns True only when the hash of the submitted code matches exactly.
    """
    return hmac.compare_digest(hash_otp(submitted), stored_hash)


def generate_otp(length: int = 6) -> str:
    """
    Generate a cryptographically secure numeric OTP of given length.
    FIX Bug-4: uses secrets.choice() instead of random.choices()
    so each digit is drawn from a cryptographically secure source.
    """
    return "".join(secrets.choice(string.digits) for _ in range(length))


def otp_expiry() -> datetime:
    """Return OTP expiry timestamp (UTC)."""
    return datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRE_MIN)


# ── Password strength ─────────────────────────────────────────
import re

STRONG_PASSWORD_RE = re.compile(
    r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()\-_=+\[\]{};:\'",.<>/?\\|`~^]).{8,}$'
)

TEMP_PASSWORD = "ChangeMeNow@123"

# ── F-14: Common / default password blocklist ─────────────────
# Passwords that satisfy the strength regex but are too predictable:
# well-known defaults, system-issued temporaries, and dictionary entries
# that follow the upper+lower+digit+symbol pattern.
# Checked case-insensitively so "password1!", "PASSWORD1!" etc. are all blocked.
BLOCKLISTED_PASSWORDS = {
    "Password1!", "Password@1", "Admin@123", "Admin1234!",
    "Thinkpad1*", "Teacher@2025!", "Hod@2025!", "Faculty@2025!",
    "ChangeMeNow@123", "Welcome@1", "Welcome1!", "P@ssw0rd",
    "Qwerty@123", "India@123", "College@1", "Summer@2025",
}

# Pre-compute lower-cased set once at import time for O(1) lookup
_BLOCKLISTED_LOWER = {p.lower() for p in BLOCKLISTED_PASSWORDS}

_BLOCKLIST_MSG = (
    "This password is too common or was a system default. "
    "Choose a different password."
)


def validate_strong_password(password: str) -> tuple[bool, str]:
    """
    Returns (is_valid: bool, error_message: str).
    Empty error_message means valid.

    Checks (in order):
      1. Minimum length
      2. Uppercase letter present
      3. Lowercase letter present
      4. Digit present
      5. Special character present
      6. F-14: Not in the common/default password blocklist (case-insensitive)
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter (A-Z)."
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter (a-z)."
    if not re.search(r'\d', password):
        return False, "Password must contain at least one number (0-9)."
    if not re.search(r'[!@#$%^&*()\-_=+\[\]{};:\'",.<>/?\\|`~^]', password):
        return False, "Password must contain at least one special character (e.g. @, #, !)."
    # F-14: blocklist check — exact match first, then case-insensitive
    if password in BLOCKLISTED_PASSWORDS:
        return False, _BLOCKLIST_MSG
    if password.lower() in _BLOCKLISTED_LOWER:
        return False, _BLOCKLIST_MSG
    return True, ""


# ── Audit helper ─────────────────────────────────────────────
def uname(user: dict) -> str:
    """Extract username string from decoded JWT payload."""
    return user.get("sub") or user.get("username") or "system"


