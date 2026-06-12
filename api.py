


# =============================================================
# api.py  —  Smart Attendance System  v9.6
#
# INTEGRATION CHANGES v9.6 (frontend bridge):
#   - All existing v9.5 endpoints kept 100% intact
#   - Added /api/* bridge routes consumed by the EduTrack
#     frontend (index.html / app.js):
#
#     POST /api/login          → wraps /auth/login, returns role
#     GET  /api/students        → get_all_students()
#     POST /api/students        → add_student()
#     DELETE /api/students/{id} → delete_student_data()
#     GET  /api/attendance/today→ get_today_attendance()
#     GET  /api/attendance/summary → get_attendance_summary()
#     POST /api/attendance/override → teacher_override()
#     GET  /api/session/status  → session status + marked list
#     POST /api/session/start   → start face recognition thread
#     POST /api/session/stop    → stop face recognition thread
#     POST /api/train           → kick off LBPH+dlib training
#     GET  /api/timetable       → period list
#     GET  /api/settings        → config thresholds
#     POST /api/settings        → update config thresholds
#     GET  /api/analytics/summary → kpi summary object
#     GET  /api/export/csv      → CSV download
#     GET  /video_feed          → MJPEG stream (unchanged)
#     GET  /app                 → serves frontend/index.html
#
# DATABASE: SQLite via database.py — single source of truth.
# =============================================================

import os
import sys
import time
import hmac
import hashlib
import base64
import json
import re
import logging
import threading
import platform
import signal
from datetime import datetime

try:
    from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import (FileResponse, HTMLResponse,
                                   StreamingResponse, JSONResponse)
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
    import uvicorn
    FASTAPI_OK = True
except ImportError:
    FASTAPI_OK = False

JWT_OK = False
try:
    import jwt as _jwt
    _jwt.encode({"t": 1}, "k", algorithm="HS256")
    JWT_OK = True
except Exception:
    pass

import config
import database as db          # SQLite — single source of truth
import attendance_session as _sess

log = logging.getLogger(__name__)
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── E Auth Merge v9.7 — new imports ──────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))
except Exception:
    pass

try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    SLOWAPI_OK = True
except ImportError:
    SLOWAPI_OK = False

try:
    from auth_utils import (
        create_access_token   as _auth_create_token,
        decode_token          as _auth_decode_token,
        get_current_user      as _auth_get_user,
        teacher_required      as _auth_teacher_req,
        admin_required        as _auth_admin_req,
        uname                 as _uname,
        # F-02: bcrypt helpers for faculty / HOD password lazy-migration
        hash_password         as _hash_password,
        verify_password       as _verify_password,
    )
    from auth_routes import router as _auth_ext_router, init_otp_table
    from user_routes import router as _user_router
    AUTH_MERGE_OK = True
except ImportError as _auth_import_err:
    AUTH_MERGE_OK = False
    log.warning("E auth merge modules not found: %s — running in legacy mode", _auth_import_err)
    # Stub fallbacks so the login code below can always reference these names
    # without an additional AUTH_MERGE_OK guard.
    def _hash_password(plain: str) -> str:          # pragma: no cover
        return plain
    def _verify_password(plain: str, hashed: str) -> bool:  # pragma: no cover
        return plain == hashed
# ─────────────────────────────────────────────────────────────


# =============================================================
# TOKEN HELPERS  (unchanged from v9.5)
# =============================================================
def _make_token(payload: dict) -> str:
    data_b = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(config.API_SECRET_KEY.encode(),
                   data_b.encode(), hashlib.sha256).hexdigest()
    return f"{data_b}.{sig}"


def _verify_token(token: str) -> dict:
    try:
        data_b, sig = token.split(".")
        expected = hmac.new(config.API_SECRET_KEY.encode(),
                            data_b.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError("invalid signature")
        data = json.loads(base64.urlsafe_b64decode(data_b).decode())
        if data.get("exp", 0) < time.time():
            raise ValueError("token expired")
        return data
    except Exception as e:
        raise HTTPException(status_code=401,
                            detail=f"Invalid token: {e}")


def create_access_token(username: str, role: str) -> str:
    payload = {
        "sub":  username,
        "role": role,
        "exp":  time.time() + config.API_TOKEN_EXPIRY_HOURS * 3600,
        "iat":  time.time(),
    }
    if JWT_OK:
        try:
            return _jwt.encode(payload, config.API_SECRET_KEY,
                               algorithm="HS256")
        except Exception:
            pass
    return _make_token(payload)


def decode_token(token: str) -> dict:
    if JWT_OK:
        try:
            return _jwt.decode(token, config.API_SECRET_KEY,
                               algorithms=["HS256"])
        except Exception:
            pass
    return _verify_token(token)


def _uname(user: dict) -> str:
    return user.get("sub") or user.get("username") or "system"


# ── F-03: admin password check with bcrypt-or-fallback ────────
def _check_admin_password(entered: str) -> bool:
    """
    Verify *entered* against config.ADMIN_PASSWORD.

    Supports two storage formats:
      1. bcrypt hash  ($2b$…) — written by auth_routes after a password reset.
         Uses verify_password() from auth_utils (bcrypt).
      2. Plain text   — legacy .env value (Admin@123 default or manually set).
         Falls back to plain equality AND immediately upgrades the stored value
         to bcrypt so the next login uses the secure path (lazy migration).

    Returns True on match, False on mismatch.
    Never raises — a failed bcrypt call is treated as mismatch.
    """
    stored = config.ADMIN_PASSWORD or ""
    if config.is_bcrypt(stored):
        # Stored as bcrypt hash — secure path
        try:
            return _verify_password(entered, stored)
        except Exception as _ve:
            log.warning("_check_admin_password: verify_password error — %s", _ve)
            return False
    else:
        # Legacy plain-text — plain compare first
        matched = (entered == stored)
        if matched:
            # Lazy upgrade: hash and persist so the next login is bcrypt
            try:
                new_hash = _hash_password(entered)
                config.ADMIN_PASSWORD       = new_hash
                os.environ["ADMIN_PASSWORD"] = new_hash
                # Persist to .env so the hash survives a restart
                try:
                    from auth_routes import _persist_admin_password_to_env
                    _persist_admin_password_to_env(new_hash)
                    log.info("F-03 lazy-migrate: admin password upgraded to bcrypt in .env")
                except Exception as _pe:
                    log.warning("F-03 lazy-migrate: could not persist admin hash to .env — %s", _pe)
            except Exception as _he:
                log.warning("F-03 lazy-migrate: could not hash admin password — %s", _he)
        return matched


# =============================================================
# PYDANTIC MODELS
# =============================================================
class LoginReq(BaseModel):
    username: str
    password: str


class StartSessionReq(BaseModel):
    period: str


class OverrideReq(BaseModel):
    student_id: str
    period:     str
    action:     str
    note:       str = ""


# ── Name validation helper ────────────────────────────────────
_NAME_RE = re.compile(r'^[A-Za-z]+( [A-Za-z]+)*$')

def _validate_name(value: str, field: str) -> str:
    """Validate a first/last name. Returns cleaned value or raises HTTPException."""
    from fastapi import HTTPException
    v = (value or "").strip()
    if not v:
        raise HTTPException(status_code=422, detail=f"{field} is required.")
    if len(v) < 3:
        raise HTTPException(status_code=422, detail=f"{field} must be at least 3 characters.")
    if len(v) > 50:
        raise HTTPException(status_code=422, detail=f"{field} must not exceed 50 characters.")
    if re.search(r'[0-9]', v):
        raise HTTPException(status_code=422, detail=f"{field} must not contain numbers.")
    if re.search(r'[^A-Za-z ]', v):
        raise HTTPException(status_code=422, detail=f"{field} must not contain special characters.")
    if not _NAME_RE.match(v):
        raise HTTPException(status_code=422, detail=f"{field} must contain only letters and single spaces.")
    return v


# ── Mobile validation helper ──────────────────────────────────
_MOBILE_RE = re.compile(r'^[6-9][0-9]{9}$')

# ── Email validation helper ───────────────────────────────────
_EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$')
_DISPOSABLE_DOMAINS = {
    "mailinator.com", "tempmail.com", "10minutemail.com", "guerrillamail.com",
    "throwam.com", "yopmail.com", "trashmail.com", "sharklasers.com",
    "guerrillamailblock.com", "grr.la", "guerrillamail.info", "spam4.me",
    "fakeinbox.com", "maildrop.cc", "dispostable.com", "mailnull.com",
    "spamgourmet.com", "trashmail.me", "throwaway.email", "discard.email",
    "mailnesia.com", "tempinbox.com", "spamex.com", "mytemp.email",
    "burnermail.io", "temp-mail.org", "getnada.com", "anonaddy.com",
}

def _validate_email(value: str, field: str = "Email", exclude_id: str = "") -> str:
    """Validate email address. Raises HTTPException on failure."""
    v = (value or "").strip()
    # Rule 1: mandatory
    if not v:
        raise HTTPException(status_code=422, detail=f"{field} is required.")
    # Rule 2: no spaces
    if " " in v:
        raise HTTPException(status_code=422,
                            detail=f"Spaces are not allowed in {field.lower()}.")
    # Rule 3: length
    if len(v) < 5:
        raise HTTPException(status_code=422,
                            detail=f"{field} is too short (minimum 5 characters).")
    if len(v) > 254:
        raise HTTPException(status_code=422,
                            detail=f"{field} is too long (maximum 254 characters).")
    # Rule 4: regex format
    if not _EMAIL_RE.match(v):
        raise HTTPException(status_code=422,
                            detail=f"Please enter a valid {field.lower()} address.")
    # Rule 5: local part check
    parts = v.split("@")
    if len(parts) != 2 or not parts[0]:
        raise HTTPException(status_code=422,
                            detail=f"Username part (before @) is missing in {field.lower()}.")
    local, domain = parts
    # Rule 6: domain check
    if not domain or domain.startswith(".") or ".." in domain:
        raise HTTPException(status_code=422,
                            detail=f"Domain part (after @) is invalid in {field.lower()}.")
    # Rule 7: invalid characters (restrict local to allowed set)
    if re.search(r'[^A-Za-z0-9._%+\-]', local):
        raise HTTPException(status_code=422,
                            detail=f"{field} contains invalid characters.")
    # Rule 8: disposable domain check
    domain_lower = domain.lower()
    if domain_lower in _DISPOSABLE_DOMAINS:
        raise HTTPException(status_code=422,
                            detail=f"Temporary/disposable email addresses are not allowed.")
    # Rule 9: duplicate check
    dup = db.check_email_duplicate(v, exclude_id)
    if dup.get("exists"):
        role = dup.get("role", "record")
        name = dup.get("name", "")
        raise HTTPException(status_code=409,
                            detail=f"This email address is already registered "
                                   f"to {name} ({role}).")
    return v



def _validate_mobile(value: str, field: str = "Mobile Number",
                     exclude_id: str = "") -> str:
    """Validate Indian mobile number. Raises HTTPException on failure."""
    v = (value or "").strip()
    if not v:
        raise HTTPException(status_code=422,
                            detail=f"{field} is required.")
    if re.search(r'[A-Za-z]', v):
        raise HTTPException(status_code=422,
                            detail=f"{field} must not contain letters.")
    if re.search(r'[^0-9]', v):
        raise HTTPException(status_code=422,
                            detail=f"{field} must not contain special characters.")
    if len(v) != 10:
        raise HTTPException(status_code=422,
                            detail=f"{field} must be exactly 10 digits.")
    if not _MOBILE_RE.match(v):
        raise HTTPException(status_code=422,
                            detail=f"{field} must start with 6, 7, 8, or 9.")
    dup = db.check_mobile_duplicate(v, exclude_id)
    if dup.get("exists"):
        role = dup.get("role", "record")
        name = dup.get("name", "")
        raise HTTPException(status_code=409,
                            detail=f"{field} {v} is already registered "
                                   f"to {name} ({role}).")
    return v


# =============================================================
# DOB VALIDATION HELPER
# =============================================================
def _validate_dob(dob_str: str, role: str, designation: str = "") -> str:
    """
    Validate date_of_birth and return the normalised YYYY-MM-DD string.
    Raises HTTPException(422) on any violation.

    Rules (mirror the frontend validateDobValue() function):
      Step 1 - required
      Step 2 - valid date format
      Step 3 - not a future date
      Step 4 - calculate age
      Step 5 - role determination
      Step 6 - role-specific age limits:
                student            : 17 <= age <= 50
                faculty / staff
                  associate prof   : age >= 32
                  otherwise        : age >= 25  (assistant professor default)
                hod                : age >= 35
    """
    from datetime import date as _date, datetime as _datetime

    v = (dob_str or "").strip()

    # Step 1 - required
    if not v:
        raise HTTPException(status_code=422, detail="Date of Birth is required.")

    # Step 2 - valid date
    parsed = None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            parsed = _datetime.strptime(v, fmt).date()
            break
        except ValueError:
            continue
    if parsed is None:
        raise HTTPException(status_code=422, detail="Please enter a valid date.")

    # Step 3 - not future
    today = _date.today()
    if parsed > today:
        raise HTTPException(status_code=422, detail="Future dates are not allowed.")

    # Step 4 - age
    age = today.year - parsed.year - (
        (today.month, today.day) < (parsed.month, parsed.day)
    )

    # Step 5 & 6 - role rules
    role_norm  = (role or "").lower()
    desig_norm = (designation or "").lower()

    if role_norm == "student":
        if age < 17 or age > 50:
            raise HTTPException(status_code=422,
                                detail="Student must be between 17 and 50 years old.")
    elif role_norm in ("faculty", "staff"):
        if "associate" in desig_norm:
            if age < 32:
                raise HTTPException(status_code=422,
                                    detail="Associate Professor must be at least 32 years old.")
        else:
            if age < 25:
                raise HTTPException(status_code=422,
                                    detail="Assistant Professor must be at least 25 years old.")
    elif role_norm == "hod":
        if age < 35:
            raise HTTPException(status_code=422,
                                detail="HOD must be at least 35 years old.")

    return parsed.strftime("%Y-%m-%d")


class AddStudentReq(BaseModel):
    register_number: str
    roll_number:     str
    first_name:      str
    last_name:       str
    gender:          str = ""
    date_of_birth:   str = ""
    department:      str = ""
    course:          str = ""
    year:            str = ""
    section:         str = "A"
    student_email:   str = ""
    parent_email:    str = ""
    student_mobile:  str = ""
    parent_mobile:   str = ""
    twin_of:         str = None
    # derived / back-compat
    name:            str = ""
    mobile:          str = ""


# Frontend-specific models
class FrontendLoginReq(BaseModel):
    email:    str = ""
    password: str
    role:     str = "admin"
    fac_id:   str = ""


class FrontendOverrideReq(BaseModel):
    student_id:  str
    period:      str
    action:      str        # mark_present | mark_absent | mark_late | mark_od
    reason:      str = ""
    modifier_id: str = ""
    category:    str = ""


# =============================================================
# TRAINING BACKGROUND TASK
# =============================================================
_train_state = {"running": False, "done": False, "error": "", "log": []}

def _run_training_bg():
    global _train_state
    _train_state["running"] = True
    _train_state["done"]    = False
    _train_state["error"]   = ""
    _train_state["log"]     = []
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            from train import train_all
            train_all()
        _train_state["log"] = buf.getvalue().split("\n")
        _train_state["done"] = True
    except Exception as e:
        _train_state["error"] = str(e)
        _train_state["done"]  = True
    finally:
        _train_state["running"] = False


# =============================================================
# APP FACTORY
# =============================================================
def create_app():
    if not FASTAPI_OK:
        raise ImportError(
            "FastAPI not installed. Run: pip install fastapi uvicorn")

    app = FastAPI(title="Smart Attendance API v9.6", version="9.6")

    # ── Rate-limiter instance — created here so it is always in scope
    # for every decorator that references _limiter inside create_app().
    # The middleware registration and app.state assignment happen later
    # (in the SLOWAPI_OK block near the bottom) but the object must exist
    # before any @_limiter.limit() decorator is evaluated.
    # If slowapi is not installed a no-op stub is used so decorated routes
    # still register without error.
    if SLOWAPI_OK:
        _limiter = Limiter(key_func=get_remote_address, default_limits=[])
    else:
        class _NoOpLimiter:
            def limit(self, *a, **kw):
                return lambda f: f
        _limiter = _NoOpLimiter()

    # ── Clean 422 validation errors → readable JSON ───────────
    try:
        from fastapi.exceptions import RequestValidationError
        from fastapi.responses import JSONResponse

        @app.exception_handler(RequestValidationError)
        async def validation_exception_handler(request, exc):
            errors = []
            for e in exc.errors():
                loc  = e.get("loc", [])
                field = loc[-1] if loc else "field"
                msg   = e.get("msg", "invalid")
                errors.append(f"{field}: {msg}")
            detail = "; ".join(errors) if errors else "Validation error"
            return JSONResponse(status_code=422, content={"detail": detail})
    except Exception:
        pass

    # F-12: CORS lockdown — never use wildcard origins.
    # Allowed origins are read from config.CORS_ORIGINS which is sourced
    # from the CORS_ORIGINS env var.  For production, set that env var to
    # your actual frontend domain only (e.g. https://yourapp.example.com).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,          # from config — never wildcard
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    )

    # ── F-15: Security response headers ───────────────────────
    # Injected on every response regardless of route or status code.
    # Placed after CORSMiddleware so CORS headers are already present
    # when this middleware runs (FastAPI middleware executes in reverse
    # registration order — last-registered runs first on the way out,
    # so registering this AFTER CORS means CORS headers are set first
    # and this middleware adds to them without conflict).
    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        # Prevent MIME-type sniffing — forces browser to honour Content-Type
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Deny framing entirely — blocks clickjacking via <iframe>
        response.headers["X-Frame-Options"] = "DENY"
        # Limit referrer information sent to third-party origins
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Restrict Permissions API surface; allow camera only from same origin
        # (face recognition requires webcam access from the served frontend)
        response.headers["Permissions-Policy"] = "camera=self"
        # HSTS: only sent over HTTPS — prevents header being set on plain HTTP
        # which would break future HTTP access to the dev server
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    # ── Auth dependencies ─────────────────────────────────────
    def get_current_user(request: Request) -> dict:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(status_code=401,
                                detail="No token provided")
        return decode_token(auth[7:])

    def teacher_required(
            user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in ("admin", "teacher", "hod",
                                    "classincharge", "faculty"):
            raise HTTPException(status_code=403,
                                detail="Teacher access required")
        return user

    def admin_required(
            user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in ("admin", "hod"):
            raise HTTPException(status_code=403,
                                detail="Admin access required")
        return user

    # =========================================================
    # ORIGINAL v9.5 ENDPOINTS (unchanged)
    # =========================================================

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "9.6"}

    @app.post("/auth/login")
    def login(req: LoginReq, request: Request):
        ip   = request.client.host if request.client else "?"
        role = None
        if (req.username == config.ADMIN_USERNAME and
                _check_admin_password(req.password)):
            role = "admin"
        elif (req.username == config.TEACHER_USERNAME and
              req.password == config.TEACHER_PASSWORD):
            role = "teacher"
        if not role:
            db.log_audit(req.username, "login_fail", "", "", ip)
            raise HTTPException(status_code=401,
                                detail="Invalid credentials")
        token = create_access_token(req.username, role)
        db.log_audit(req.username, "login_ok", "", role, ip)
        return {"access_token": token, "token_type": "bearer",
                "role": role}

    @app.get("/students")
    def list_students(_: dict = Depends(get_current_user)):
        return db.get_all_students()

    @app.post("/students")
    def add_student(req: AddStudentReq, request: Request,
                    user: dict = Depends(teacher_required)):
        sid = f"STU_{req.roll_number.upper()}"
        ok  = db.add_student(
            student_id=sid, name=req.name,
            roll_number=req.roll_number.lower(),
            section=req.section, mobile=req.mobile,
            twin_of=req.twin_of)
        db.log_audit(_uname(user), "add_student", sid, req.name,
                     request.client.host if request.client else "?")
        if not ok:
            raise HTTPException(status_code=409,
                                detail="Student already exists")
        return {"student_id": sid, "status": "created"}

    @app.delete("/students/{student_id}")
    def delete_student(student_id: str, request: Request,
                       user: dict = Depends(admin_required)):
        db.delete_student_data(student_id)
        db.log_audit(_uname(user), "delete_student", student_id,
                     "", request.client.host if request.client else "?")
        return {"status": "deactivated"}

    @app.get("/attendance/today")
    def today(period: str = None,
              _: dict = Depends(get_current_user)):
        return db.get_today_attendance(period)

    @app.get("/attendance/summary")
    def summary(days: int = 30,
                _: dict = Depends(get_current_user)):
        return db.get_attendance_summary(days)

    @app.post("/attendance/override")
    def override(req: OverrideReq, request: Request,
                 user: dict = Depends(teacher_required)):
        db.teacher_override(req.student_id, req.period,
                            req.action, req.note)
        db.log_audit(_uname(user), "override",
                     req.student_id, req.action,
                     request.client.host if request.client else "?")
        return {"status": "done"}

    @app.get("/attendance/yesterday")
    def yesterday(period: str = None,
                  _: dict = Depends(get_current_user)):
        from datetime import timedelta
        d = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        return db.get_attendance_by_date(d, period)

    @app.get("/attendance/date/{date_str}")
    def by_date(date_str: str, period: str = None,
                _: dict = Depends(get_current_user)):
        return db.get_attendance_by_date(date_str, period)

    @app.get("/analytics/engine")
    def engine_stats(days: int = 7,
                     _: dict = Depends(get_current_user)):
        return db.get_engine_stats(days)

    @app.get("/analytics/period")
    def period_stats(_: dict = Depends(get_current_user)):
        return db.get_period_stats()

    @app.get("/analytics/twins")
    def twin_log(days: int = 7,
                 _: dict = Depends(get_current_user)):
        return db.get_twin_analysis_log(days)

    @app.get("/timetable")
    def timetable(_: dict = Depends(get_current_user)):
        return db.get_timetable()

    @app.get("/settings")
    def get_settings(_: dict = Depends(get_current_user)):
        return {
            "LBPH_THRESHOLD":          config.LBPH_THRESHOLD,
            "DLIB_DISTANCE":           config.DLIB_DISTANCE,
            "MIN_CONFIDENCE_PCT":      config.MIN_CONFIDENCE_PCT,
            "CONFIRM_FRAMES_REQUIRED": config.CONFIRM_FRAMES_REQUIRED,
            "LIVENESS_THRESHOLD":      config.LIVENESS_THRESHOLD,
            "LIVENESS_ON":             config.LIVENESS_ON,
            "CAMERA_INDEX":            config.CAMERA_INDEX,
        }

    @app.post("/settings")
    def save_settings(data: dict,
                      user: dict = Depends(admin_required)):
        for key, cast in {
            "LBPH_THRESHOLD": float,
            "DLIB_DISTANCE": float,
            "MIN_CONFIDENCE_PCT": float,
            "CONFIRM_FRAMES_REQUIRED": int,
            "LIVENESS_THRESHOLD": float,
            "LIVENESS_ON": bool,
            "CAMERA_INDEX": int,
        }.items():
            if key in data:
                setattr(config, key, cast(data[key]))
        return {"status": "ok"}

    @app.get("/export/csv")
    def export_csv(_: dict = Depends(get_current_user)):
        import csv, io
        today_str = datetime.now().strftime("%Y-%m-%d")
        rows      = db.get_today_attendance()
        out       = io.StringIO()
        w = csv.DictWriter(out, fieldnames=[
            "name", "roll_number", "period", "date",
            "time", "confidence", "engine"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
        out.seek(0)
        return StreamingResponse(
            iter([out.read()]),
            media_type="text/csv",
            headers={"Content-Disposition":
                     f"attachment; filename=attendance_{today_str}.csv"})

    @app.post("/session/start")
    def session_start(req: StartSessionReq,
                      user: dict = Depends(teacher_required)):
        state = _sess._SESSION_STATE
        t = state.get("thread")
        if t and not t.is_alive():
            state["running"] = False
            state["thread"]  = None
        if state["running"]:
            raise HTTPException(
                status_code=409,
                detail="Session already running. Stop it first.")
        period = req.period.strip()
        if not period:
            raise HTTPException(status_code=400,
                                detail="Period name is required")
        result = _sess.start_session(period)
        if not result["ok"]:
            raise HTTPException(status_code=500,
                                detail=result.get("error", "Start failed"))
        db.log_audit(_uname(user), "session_start", period)
        return {
            "status": "started",
            "period": period,
            "stream": f"http://localhost:{config.API_PORT}/video_feed",
        }

    @app.post("/session/stop")
    def session_stop(user: dict = Depends(teacher_required)):
        _sess.stop_session()
        db.log_audit(_uname(user), "session_stop",
                     _sess._SESSION_STATE.get("period", ""))
        return {"status": "stopped"}

    @app.get("/session/status")
    def session_status(_: dict = Depends(get_current_user)):
        state  = _sess.get_status()
        period = state.get("period")
        marked_rows: list = []
        if period:
            try:
                marked_rows = db.get_today_attendance(period) or []
            except Exception:
                pass
        already_marked = [
            {
                "student_id": r.get("student_id", ""),
                "name":       r.get("name", ""),
                "time":       str(r.get("time", ""))[:8],
                "confidence": int(float(r.get("confidence", 0)) * 100),
                "engine":     r.get("engine", ""),
            }
            for r in marked_rows
        ]
        total_students = 0
        try:
            total_students = len(db.get_all_students())
        except Exception:
            pass
        return {
            "running":        state.get("running", False),
            "period":         period,
            "started_at":     state.get("started_at"),
            "marked_count":   len(marked_rows),
            "total_students": total_students,
            "absent_count":   max(0, total_students - len(marked_rows)),
            "already_marked": already_marked,
            "error":          state.get("error") or "",
        }

    @app.get("/video_feed")
    def video_feed():
        return StreamingResponse(
            _sess.generate_frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma":        "no-cache",
                "Expires":       "0",
            }
        )

    # =========================================================
    # /api/* BRIDGE ENDPOINTS  (new in v9.6 — for EduTrack frontend)
    # =========================================================

    # ── Login ─────────────────────────────────────────────────
    @app.post("/api/login")
    @(_limiter.limit("10/minute") if SLOWAPI_OK else lambda f: f)
    def api_login(req: FrontendLoginReq, request: Request):
        """
        Accepts the EduTrack frontend login payload.
        Supports roles: admin, hod, classincharge, teacher, faculty.
        HOD:          email=suganyainbox32@gmail.com      + HOD_PASSWORD
        ClassIncharge: email=g3260998@gmail.com + INCHARGE_PASSWORD
        Teacher:      email=teacher@college.edu   + TEACHER_PASSWORD
        Admin:        email=suganyainbox25@gmail.com     + ADMIN_PASSWORD
        Faculty:      fac_id (any FAC00x)         + FACULTY_DEFAULT_PASSWORD
        Returns JWT token + role for the frontend to store.
        F-04: Rate-limited (10/minute per IP) + account lockout after 5 failures.
        """
        ip = request.client.host if request.client else "?"
        role = None
        username = ""
        display_name = ""

        # ── F-04: Brute-force lockout — determine identifier ──
        _bf_identifier = (req.fac_id.strip().upper() if req.fac_id else req.email.strip().lower())

        # Check lockout state BEFORE any credential verification
        _fail_count, _is_locked, _lock_until = db.get_login_fail_count(_bf_identifier)
        if _is_locked:
            _now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if _lock_until and _lock_until > _now_str:
                # Still within lock window — reject immediately
                db.log_audit(_bf_identifier, "login_blocked", "", "brute_force_lockout", ip)
                raise HTTPException(
                    status_code=429,
                    detail=f"Account locked due to too many failed attempts. "
                           f"Try again after {_lock_until}."
                )
            else:
                # Lock window has expired — auto-unlock and allow attempt
                db.reset_login_fail(_bf_identifier)

        # ── Faculty portal login (fac_id provided) ────────────
        if req.fac_id:
            fac_id_upper = req.fac_id.strip().upper()
            fac = db.get_faculty_by_id(fac_id_upper)
            if fac is None:
                db.log_audit(fac_id_upper, "login_fail", "", "faculty", ip)
                db.increment_login_fail(fac_id_upper)
                raise HTTPException(status_code=401,
                                    detail="Faculty ID not found. Contact Admin.")
            # ── F-02: bcrypt-or-fallback password check ───────────
            # If the stored value is a bcrypt hash ($2b$…) use verify_password().
            # Otherwise fall back to plain-text equality and immediately re-hash
            # the password so the row is migrated on first successful login.
            stored_password = (fac.get("password") or "Staff@123").strip()
            entered = req.password.strip()

            if stored_password.startswith("$2b$"):
                # Already hashed — use bcrypt verify
                _authed = _verify_password(entered, stored_password)
            else:
                # Legacy plain-text row — plain compare
                _authed = (entered == stored_password)
                if _authed:
                    # Lazy migration: upgrade the row to bcrypt now
                    try:
                        _new_hash = _hash_password(entered)
                        db.update_faculty_password(fac_id_upper, _new_hash)
                        log.info("F-02 lazy-migrate: hashed password for faculty %s", fac_id_upper)
                    except Exception as _lm_err:
                        log.warning("F-02 lazy-migrate: could not hash faculty %s — %s",
                                    fac_id_upper, _lm_err)

            if not _authed:
                db.log_audit(fac_id_upper, "login_fail", "", "faculty", ip)
                db.increment_login_fail(fac_id_upper)
                raise HTTPException(
                    status_code=401,
                    detail="Invalid Faculty ID or password."
                )
            db.reset_login_fail(fac_id_upper)
            token = create_access_token(fac_id_upper, "faculty")
            db.log_audit(fac_id_upper, "login_ok", "", "faculty", ip)
            return {
                "access_token": token, "token_type": "bearer",
                "role": "faculty",
                "fac_id": fac_id_upper,
                "name": fac.get("name", fac_id_upper),
                "username": fac.get("name", fac_id_upper),
            }

        # ── Admin / HOD login ─────────────────────────────────
        # Priority:
        #   1. Admin  : email ==suganyainbox25@gmail.com + ADMIN_PASSWORD (config)
        #   2. HOD DB : hod_id or email found in hods table + matching password
        #   3. HOD    : legacy email hod@college.edu + HOD_PASSWORD (config fallback)
        email = req.email.strip().lower()

        ALLOWED_ADMIN_EMAILS = {
            "suganyainbox25@gmail.com",
            config.ADMIN_USERNAME.lower(),
            f"{config.ADMIN_USERNAME.lower()}@college.edu",
        }

        if email in ALLOWED_ADMIN_EMAILS:
            # F-03: bcrypt-aware admin password check (bcrypt hash or plain-text lazy upgrade)
            if _check_admin_password(req.password):
                role = "admin"
                username = "suganyainbox25@gmail.com"
                display_name ="ADMIN"
            else:
                db.log_audit(email, "login_fail", "", "", ip)
                db.increment_login_fail(email)
                raise HTTPException(status_code=401,
                                    detail="Invalid credentials. Wrong password for Admin.")
            db.reset_login_fail(email)

        else:
            # ── Try HOD table (DB-managed HODs) ───────────────
            hod_record = None
            try:
                from api_hod import get_hod_by_credentials
                # identifier can be hod_id (e.g. HOD001) or email
                identifier = req.email.strip()
                hod_record = get_hod_by_credentials(identifier.upper(), req.password) \
                          or get_hod_by_credentials(identifier.lower(), req.password)
            except Exception as _hod_err:
                log.debug("HOD DB lookup failed: %s", _hod_err)

            if hod_record:
                role         = "hod"
                username     = hod_record.get("email") or hod_record.get("hod_id")
                display_name = hod_record.get("name", "HOD")
                hod_id_val   = hod_record.get("hod_id", "")
                # ── F-02: HOD lazy migration ───────────────────
                # get_hod_by_credentials already accepts both plain and bcrypt.
                # If the login succeeded with a plain-text password, upgrade the
                # row to bcrypt now so future logins use the secure path.
                if hod_id_val:
                    try:
                        import sqlite3 as _sq3
                        with _sq3.connect(db.DB_PATH, timeout=10) as _hod_conn:
                            _hod_conn.row_factory = _sq3.Row
                            _stored_hod_pw = (_hod_conn.execute(
                                "SELECT password FROM hods WHERE hod_id=?",
                                (hod_id_val,)
                            ).fetchone() or {}).get("password", "") or ""
                        if _stored_hod_pw and not _stored_hod_pw.startswith("$2b$"):
                            _new_hod_hash = _hash_password(req.password)
                            db.update_hod_password(hod_id_val, _new_hod_hash)
                            log.info("F-02 lazy-migrate: hashed password for HOD %s", hod_id_val)
                    except Exception as _hod_lm_err:
                        log.warning("F-02 lazy-migrate: could not hash HOD %s — %s",
                                    hod_id_val, _hod_lm_err)
                db.reset_login_fail(_bf_identifier)
            elif email == "hod@college.edu" and req.password == config.HOD_PASSWORD:
                # Legacy single-HOD fallback (backward compatible)
                role         = "hod"
                username     = "hod@college.edu"
                display_name = "HOD"
                hod_id_val   = ""
                db.reset_login_fail(_bf_identifier)
            else:
                db.log_audit(email or "unknown", "login_fail", "", "", ip)
                db.increment_login_fail(_bf_identifier)
                raise HTTPException(status_code=401,
                                    detail="Invalid credentials. Unrecognised username or wrong password.")

            if role == "hod":
                token = create_access_token(username, role)
                db.log_audit(username, "login_ok", "", role, ip)
                return {
                    "access_token": token, "token_type": "bearer",
                    "role": role,
                    "username": username,
                    "name": display_name,
                    "hod_id": hod_id_val,
                    "dept": hod_record.get("dept", "") if hod_record else "",
                }

        if not role:
            db.log_audit(req.email or "unknown", "login_fail", "", "", ip)
            db.increment_login_fail(_bf_identifier)
            raise HTTPException(status_code=401,
                                detail="Invalid credentials")

        token = create_access_token(username, role)
        db.log_audit(username, "login_ok", "", role, ip)
        return {
            "access_token": token, "token_type": "bearer",
            "role": role,
            "username": username,
            "name": display_name or username,
        }

    # ── Verify Token ──────────────────────────────────────────
    @app.get("/api/verify-token")
    def api_verify_token(user: dict = Depends(get_current_user)):
        """
        Validates the JWT token from Authorization header.
        Returns user info if token is valid; 401 if not.
        Used by frontend on page reload to restore session.
        """
        return {
            "valid": True,
            "role": user.get("role", "admin"),
            "username": user.get("sub", ""),
            "fac_id": user.get("fac_id", ""),
            "name": user.get("name", user.get("sub", "")),
        }

    # ── Students ──────────────────────────────────────────────
    @app.get("/api/students")
    def api_list_students(_: dict = Depends(get_current_user)):
        rows = db.get_all_students()
        return [dict(r) for r in rows]

    # IMPORTANT: /check/ route must come BEFORE /{student_id} to avoid conflict
    @app.get("/api/students/check/{register_number}")
    def api_check_student(register_number: str,
                          _: dict = Depends(get_current_user)):
        """Check if a student with this register number already exists."""
        sid = f"STU_{register_number.upper()}"
        s = db.get_student(sid)
        if s:
            return {"exists": True, "student": dict(s)}
        return {"exists": False}

    @app.get("/api/check/mobile/{mobile}")
    def api_check_mobile(mobile: str,
                         exclude_id: str = "",
                         _: dict = Depends(get_current_user)):
        """Check if a mobile number is already registered (duplicate check)."""
        result = db.check_mobile_duplicate(mobile.strip(), exclude_id)
        return result

    @app.get("/api/check/email/{email:path}")
    def api_check_email(email: str,
                        exclude_id: str = "",
                        _: dict = Depends(get_current_user)):
        """Check if an email address is already registered (duplicate check)."""
        result = db.check_email_duplicate(email.strip(), exclude_id)
        return result

    # ── Enrollment Email OTP — Send ───────────────────────────
    class EnrollOTPSendReq(BaseModel):
        email: str

    @app.post("/api/enroll/send-otp")
    def api_enroll_send_otp(
        req: EnrollOTPSendReq,
        request: Request,
    ):
        """
        Enrollment Email OTP — Step 1: Send a 6-digit OTP to the student email.
        Validates format, checks duplicate, enforces resend limit (max 3).
        OTP stored as SHA-256 hash (never plain text). Expiry = 5 minutes.
        """
        import hashlib, secrets as _sec, re, sqlite3 as _sqlite3, os, smtplib
        from datetime import datetime, timezone, timedelta
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from utils.email_utils import _smtp_cfg

        email_addr = req.email.strip().lower()

        # Format check
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email_addr):
            raise HTTPException(status_code=422, detail="Invalid email format.")

        # Duplicate check — cannot enroll with an already-registered email
        dup = db.check_email_duplicate(email_addr)
        if dup.get("exists"):
            raise HTTPException(status_code=409, detail="Email already registered.")

        DB_PATH = os.path.join(config.BASE_DIR, "attendance.db")

        # Bootstrap enroll_otp_sessions table if not exists
        with _sqlite3.connect(DB_PATH, timeout=15) as _conn:
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("""
                CREATE TABLE IF NOT EXISTS enroll_otp_sessions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    email        TEXT NOT NULL,
                    otp_hash     TEXT NOT NULL,
                    expires_at   TEXT NOT NULL,
                    attempts     INTEGER DEFAULT 0,
                    resend_count INTEGER DEFAULT 0,
                    is_verified  INTEGER DEFAULT 0,
                    created_at   TEXT DEFAULT (datetime('now','utc'))
                )
            """)
            _conn.commit()

        # Check existing resend count for this email
        with _sqlite3.connect(DB_PATH, timeout=10) as _conn:
            _conn.row_factory = _sqlite3.Row
            existing = _conn.execute(
                """SELECT resend_count FROM enroll_otp_sessions
                   WHERE email=? AND is_verified=0
                   ORDER BY created_at DESC LIMIT 1""",
                (email_addr,)
            ).fetchone()

        resend_count = (existing["resend_count"] if existing else 0)
        if resend_count >= 3:
            raise HTTPException(
                status_code=429,
                detail="Maximum resend attempts (3) reached. Please use a different email or contact support."
            )

        # Generate OTP and hash it (SHA-256 — never store plain text)
        otp_plain  = "".join(_sec.choice("0123456789") for _ in range(6))
        otp_hash   = hashlib.sha256(otp_plain.encode()).hexdigest()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

        # Invalidate old sessions and create new one
        with _sqlite3.connect(DB_PATH, timeout=10) as _conn:
            _conn.execute(
                "UPDATE enroll_otp_sessions SET is_verified=2 WHERE email=? AND is_verified=0",
                (email_addr,)
            )
            _conn.execute(
                """INSERT INTO enroll_otp_sessions
                   (email, otp_hash, expires_at, resend_count)
                   VALUES (?, ?, ?, ?)""",
                (email_addr, otp_hash, expires_at, resend_count + (1 if existing else 0))
            )
            _conn.commit()

        # Send OTP email via existing SMTP config
        cfg = _smtp_cfg()
        if not cfg["user"] or not cfg["pass"]:
            raise HTTPException(status_code=503, detail="SMTP not configured. Contact administrator.")

        html_body = f"""
        <div style="font-family:'Segoe UI',sans-serif;max-width:480px;margin:0 auto;
                    background:#f8fafc;border-radius:16px;padding:32px;">
          <div style="text-align:center;margin-bottom:24px;">
            <h2 style="background:linear-gradient(135deg,#06b6d4,#3b82f6);
                       -webkit-background-clip:text;-webkit-text-fill-color:transparent;
                       font-size:24px;font-weight:800;margin:0;">EduTrack Pro</h2>
            <p style="color:#64748b;font-size:13px;margin:4px 0 0;">
              Smart Attendance &amp; Face Recognition System
            </p>
          </div>
          <div style="background:#fff;border-radius:12px;padding:28px;
                      border:1.5px solid #e2e8f0;text-align:center;">
            <p style="font-size:16px;font-weight:600;color:#0f172a;margin-bottom:8px;">
              Email Verification OTP
            </p>
            <p style="color:#64748b;font-size:13px;margin-bottom:20px;">
              Use this code to verify your email during student enrollment.<br>
              This code expires in <strong>5 minutes</strong>.
              Do not share it with anyone.
            </p>
            <div style="font-size:36px;font-weight:800;letter-spacing:10px;
                        color:#06b6d4;background:#f0fdfe;border-radius:10px;
                        padding:16px 24px;display:inline-block;">{otp_plain}</div>
            <p style="color:#94a3b8;font-size:12px;margin-top:20px;">
              If you did not request this, please ignore this email.
            </p>
          </div>
          <p style="text-align:center;color:#cbd5e1;font-size:11px;margin-top:16px;">
            &copy; EduTrack Pro &mdash; Smart Attendance System. All rights reserved.
          </p>
        </div>
        """
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "EduTrack Pro — Email Verification OTP"
        msg["From"]    = f"EduTrack Pro — Smart Attendance <{cfg['user']}>"
        msg["To"]      = email_addr
        msg.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(cfg["host"], cfg["port"]) as srv:
                srv.ehlo(); srv.starttls(); srv.ehlo()
                srv.login(cfg["user"], cfg["pass"])
                srv.sendmail(cfg["user"], email_addr, msg.as_string())
        except Exception as exc:
            log.error("[EnrollOTP] Send failed to %s: %s", email_addr, exc)
            # Rollback the session we just inserted
            with _sqlite3.connect(DB_PATH, timeout=10) as _conn:
                _conn.execute(
                    "DELETE FROM enroll_otp_sessions WHERE email=? AND otp_hash=?",
                    (email_addr, otp_hash)
                )
                _conn.commit()
            raise HTTPException(status_code=503, detail="Unable to send OTP. Please try again.")

        log.info("[EnrollOTP] OTP sent to %s", email_addr)
        return {"status": "sent", "message": "OTP sent to your email address."}


    # ── Enrollment Email OTP — Verify ─────────────────────────
    class EnrollOTPVerifyReq(BaseModel):
        email: str
        otp:   str

    @app.post("/api/enroll/verify-otp")
    def api_enroll_verify_otp(
        req: EnrollOTPVerifyReq,
    ):
        """
        Enrollment Email OTP — Step 2: Verify the submitted OTP.
        Max 5 attempts enforced. Hash comparison only (never plain text).
        On success: marks session is_verified=1 (consumed when student is saved).
        """
        import hashlib, sqlite3 as _sqlite3, os
        from datetime import datetime, timezone

        OTP_MAX  = 5
        email_addr = req.email.strip().lower()
        DB_PATH    = os.path.join(config.BASE_DIR, "attendance.db")

        with _sqlite3.connect(DB_PATH, timeout=10) as _conn:
            _conn.row_factory = _sqlite3.Row
            record = _conn.execute(
                """SELECT * FROM enroll_otp_sessions
                   WHERE email=? AND is_verified=0
                   ORDER BY created_at DESC LIMIT 1""",
                (email_addr,)
            ).fetchone()

        if not record:
            raise HTTPException(status_code=400, detail="No active OTP found. Please request a new one.")

        record = dict(record)

        # Attempt limit
        if record["attempts"] >= OTP_MAX:
            with _sqlite3.connect(DB_PATH, timeout=10) as _conn:
                _conn.execute("UPDATE enroll_otp_sessions SET is_verified=2 WHERE id=?", (record["id"],))
                _conn.commit()
            raise HTTPException(
                status_code=429,
                detail=f"OTP locked after {OTP_MAX} failed attempts. Please request a new OTP."
            )

        # Expiry check
        expires_at = datetime.fromisoformat(record["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= expires_at:
            with _sqlite3.connect(DB_PATH, timeout=10) as _conn:
                _conn.execute("UPDATE enroll_otp_sessions SET is_verified=2 WHERE id=?", (record["id"],))
                _conn.commit()
            raise HTTPException(status_code=400, detail="OTP expired. Please request a new one.")

        # Hash comparison — never compare plain text
        submitted_hash = hashlib.sha256(req.otp.strip().encode()).hexdigest()
        if submitted_hash != record["otp_hash"]:
            new_attempts = record["attempts"] + 1
            remaining    = OTP_MAX - new_attempts
            with _sqlite3.connect(DB_PATH, timeout=10) as _conn:
                _conn.execute(
                    "UPDATE enroll_otp_sessions SET attempts=? WHERE id=?",
                    (new_attempts, record["id"])
                )
                _conn.commit()
            if remaining <= 0:
                with _sqlite3.connect(DB_PATH, timeout=10) as _conn2:
                    _conn2.execute("UPDATE enroll_otp_sessions SET is_verified=2 WHERE id=?", (record["id"],))
                    _conn2.commit()
                raise HTTPException(
                    status_code=429,
                    detail=f"OTP locked after {OTP_MAX} failed attempts. Please request a new OTP."
                )
            raise HTTPException(
                status_code=400,
                detail=f"Invalid OTP. {remaining} attempt(s) remaining."
            )

        # Mark as verified
        with _sqlite3.connect(DB_PATH, timeout=10) as _conn:
            _conn.execute("UPDATE enroll_otp_sessions SET is_verified=1 WHERE id=?", (record["id"],))
            _conn.commit()

        log.info("[EnrollOTP] Email verified for enrollment: %s", email_addr)
        return {"status": "verified", "message": "Email verified successfully."}


    # ── Helper: check enrollment OTP verified status ──────────
    def _check_enroll_email_verified(email: str) -> bool:
        """Return True only if a verified (non-expired) OTP session exists for email."""
        import sqlite3 as _sqlite3, os
        from datetime import datetime, timezone
        if not email:
            return False
        DB_PATH = os.path.join(config.BASE_DIR, "attendance.db")
        try:
            with _sqlite3.connect(DB_PATH, timeout=10) as _conn:
                _conn.row_factory = _sqlite3.Row
                record = _conn.execute(
                    """SELECT expires_at FROM enroll_otp_sessions
                       WHERE email=? AND is_verified=1
                       ORDER BY created_at DESC LIMIT 1""",
                    (email.strip().lower(),)
                ).fetchone()
            if not record:
                return False
            expires_at = datetime.fromisoformat(record["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) < expires_at
        except Exception:
            return False

    @app.patch("/api/students/{student_id}/deactivate")
    def api_deactivate_student(student_id: str, request: Request,
                               user: dict = Depends(admin_required)):
        """Soft-delete: mark student Inactive, preserve all attendance records.

        FIX: Sets BOTH status='Inactive' AND active=0 so the student is hidden
        from get_all_students() (which filters WHERE active=1).  Previously only
        status was updated, leaving active=1 and keeping the student visible.
        The existence check now searches without an active filter so students
        that are already hidden (active=0) can still be explicitly deactivated
        via this endpoint rather than returning a spurious 404.

        ROUTE ORDER FIX: This route MUST be registered before the wildcard
        @app.get("/api/students/{student_id}") route.  FastAPI matches routes
        top-down; if the wildcard is registered first it captures the URL
        "/api/students/STU_XX/deactivate" treating "STU_XX/deactivate" as the
        student_id, finds no match, and returns 404 before this handler runs.
        """
        import sqlite3 as _sq3, os as _os
        _db_path = _os.path.join(__import__('config').BASE_DIR, "attendance.db")
        try:
            with _sq3.connect(_db_path, timeout=10) as _c:
                _c.execute("PRAGMA journal_mode=WAL")
                row = _c.execute(
                    "SELECT student_id FROM students WHERE student_id=?",
                    (student_id,)
                ).fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Student not found")
                _c.execute(
                    "UPDATE students SET status='Inactive', active=0 WHERE student_id=?",
                    (student_id,)
                )
                _c.commit()
            db.log_audit(_uname(user), "deactivate_student", student_id,
                         "", request.client.host if request.client else "?")
            return {"status": "deactivated", "student_id": student_id,
                    "message": "Student marked Inactive. All records preserved."}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/students/{student_id}")
    def api_get_student(student_id: str,
                        _: dict = Depends(get_current_user)):
        """Return a single student or 404."""
        s = db.get_student(student_id)
        if not s:
            raise HTTPException(status_code=404, detail="Student not found")
        return dict(s)

    @app.put("/api/students/{student_id}")
    def api_update_student(student_id: str, req: AddStudentReq,
                           request: Request,
                           user: dict = Depends(teacher_required)):
        """Update an existing student's data (used when user chooses Y to retrain)."""
        # Strict name validation (backend)
        req.first_name = _validate_name(req.first_name, "First Name")
        req.last_name  = _validate_name(req.last_name,  "Last Name")
        # Strict mobile validation (backend) — exclude self from duplicate check
        if req.student_mobile:
            req.student_mobile = _validate_mobile(req.student_mobile, "Student Mobile", student_id)
        if req.parent_mobile:
            req.parent_mobile  = _validate_mobile(req.parent_mobile,  "Parent Mobile",  student_id)
        # Strict email validation (backend) — exclude self from duplicate check
        if req.student_email:
            req.student_email = _validate_email(req.student_email, "Student Email", student_id)
        if req.parent_email:
            req.parent_email  = _validate_email(req.parent_email,  "Parent Email",  student_id)
        # Strict DOB validation (backend, Step 7-8 of validation flow)
        req.date_of_birth = _validate_dob(req.date_of_birth, "student")
        full_name = req.name or f"{req.first_name} {req.last_name}".strip()
        import sqlite3 as _sq3, os as _os
        from datetime import datetime as _dt
        from contextlib import contextmanager as _cm
        _db_path = _os.path.join(__import__('config').BASE_DIR, "attendance.db")

        @_cm
        def _conn():
            c = _sq3.connect(_db_path, timeout=15)
            c.row_factory = _sq3.Row
            c.execute("PRAGMA journal_mode=WAL")
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise
            finally:
                c.close()

        # FIX: honour the status field sent by the edit form instead of
        # hardcoding 'Active'.  Keep the active INTEGER column in sync so
        # get_all_students() and attendance queries see a consistent state.
        _req_status = (getattr(req, "status", None) or "Active").strip()
        if _req_status not in ("Active", "Inactive"):
            _req_status = "Active"
        _req_active = 1 if _req_status == "Active" else 0

        try:
            with _conn() as c:
                c.execute("""
                    UPDATE students SET
                        name=?, register_number=?, roll_number=?,
                        first_name=?, last_name=?, gender=?,
                        date_of_birth=?, department=?, course=?,
                        year=?, section=?, student_email=?,
                        parent_email=?, student_mobile=?, parent_mobile=?,
                        status=?, active=?, twin_of=?
                    WHERE student_id=?
                """, (full_name,
                      req.register_number, req.roll_number.lower(),
                      req.first_name, req.last_name, req.gender,
                      req.date_of_birth, req.department, req.course,
                      req.year, req.section,
                      req.student_email, req.parent_email,
                      req.student_mobile, req.parent_mobile,
                      _req_status, _req_active,
                      req.twin_of, student_id))
            db.log_audit(_uname(user), "update_student", student_id, full_name,
                         request.client.host if request.client else "?")
            return {"student_id": student_id, "status": "updated",
                    "message": f"Student {full_name} updated successfully."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/students")
    def api_add_student(req: AddStudentReq, request: Request,
                        user: dict = Depends(teacher_required)):
        # Strict name validation (backend)
        req.first_name = _validate_name(req.first_name, "First Name")
        req.last_name  = _validate_name(req.last_name,  "Last Name")
        # Strict mobile validation (backend)
        if req.student_mobile:
            req.student_mobile = _validate_mobile(req.student_mobile, "Student Mobile")
        if req.parent_mobile:
            req.parent_mobile  = _validate_mobile(req.parent_mobile,  "Parent Mobile")
        # Strict email validation (backend)
        if req.student_email:
            req.student_email = _validate_email(req.student_email, "Student Email")
        if req.parent_email:
            req.parent_email  = _validate_email(req.parent_email,  "Parent Email")
        # Strict DOB validation (backend, Step 7-8 of validation flow)
        req.date_of_birth = _validate_dob(req.date_of_birth, "student")

        # ── Email OTP Verification enforcement (Rule 17) ──────────
        # Student email is required and must be OTP-verified before enrollment.
        if not req.student_email:
            raise HTTPException(
                status_code=422,
                detail="Student Email is required. Please enter and verify your email address."
            )
        if not _check_enroll_email_verified(req.student_email):
            raise HTTPException(
                status_code=403,
                detail="Student email has not been verified. Please complete email OTP verification before enrolling."
            )

        full_name = req.name or f"{req.first_name} {req.last_name}".strip()
        if not full_name:
            raise HTTPException(status_code=422, detail="first_name and last_name are required")
        sid = f"STU_{req.register_number.upper()}"
        ok  = db.add_student(
            student_id      = sid,
            name            = full_name,
            register_number = req.register_number,
            roll_number     = req.roll_number.lower(),
            first_name      = req.first_name,
            last_name       = req.last_name,
            gender          = req.gender,
            date_of_birth   = req.date_of_birth,
            department      = req.department,
            course          = req.course,
            year            = req.year,
            section         = req.section,
            student_email   = req.student_email,
            parent_email    = req.parent_email,
            student_mobile  = req.student_mobile,
            parent_mobile   = req.parent_mobile,
            status          = "Active",
            twin_of         = req.twin_of,
        )
        db.log_audit(_uname(user), "add_student", sid, full_name,
                     request.client.host if request.client else "?")
        if not ok:
            raise HTTPException(status_code=409,
                                detail="Student already exists")
        # Consume the verified OTP session so it cannot be reused
        import sqlite3 as _sqlite3, os as _os
        try:
            _db_path = _os.path.join(config.BASE_DIR, "attendance.db")
            with _sqlite3.connect(_db_path, timeout=10) as _oc:
                _oc.execute(
                    """UPDATE enroll_otp_sessions SET is_verified=3
                       WHERE email=? AND is_verified=1""",
                    (req.student_email.strip().lower(),)
                )
                _oc.commit()
        except Exception as _oe:
            log.warning("[EnrollOTP] Could not consume OTP session: %s", _oe)
        return {"student_id": sid, "status": "created",
                "message": f"Student {full_name} enrolled successfully."}

    @app.delete("/api/students/{student_id}")
    def api_delete_student(student_id: str, request: Request,
                           user: dict = Depends(admin_required)):
        try:
            db.delete_student_data(student_id)
            db.log_audit(_uname(user), "delete_student", student_id,
                         "", request.client.host if request.client else "?")
            return {"status": "deleted", "student_id": student_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── Attendance ────────────────────────────────────────────
    @app.get("/api/attendance/today")
    def api_today(period: str = None,
                  _: dict = Depends(get_current_user)):
        rows = db.get_today_attendance(period)
        return [dict(r) for r in rows]

    @app.get("/api/attendance/summary")
    def api_summary(days: int = 30,
                    _: dict = Depends(get_current_user)):
        rows = db.get_attendance_summary(days)
        return [dict(r) for r in rows]

    @app.get("/api/attendance/date/{date_str}")
    def api_by_date(date_str: str, period: str = None,
                    _: dict = Depends(get_current_user)):
        rows = db.get_attendance_by_date(date_str, period)
        return [dict(r) for r in rows]

    @app.post("/api/attendance/override")
    def api_override(req: FrontendOverrideReq, request: Request,
                     user: dict = Depends(teacher_required)):
        """
        Frontend override — supports richer payload from EduTrack UI.
        Internally maps to the same db.teacher_override() call.
        """
        note = req.reason
        if req.category and req.category != "—":
            note = f"[{req.category}] {note}".strip()
        if req.modifier_id:
            note = f"{note} (by {req.modifier_id})".strip()

        db.teacher_override(req.student_id, req.period,
                            req.action, note)
        db.log_audit(
            _uname(user), "override",
            req.student_id,
            f"{req.action} — {note}",
            request.client.host if request.client else "?")
        return {"status": "done", "message": "Override saved to database"}

    # ── Session ───────────────────────────────────────────────
    @app.post("/api/session/start")
    def api_session_start(req: StartSessionReq,
                          user: dict = Depends(teacher_required)):
        state = _sess._SESSION_STATE
        t = state.get("thread")
        if t and not t.is_alive():
            state["running"] = False
            state["thread"]  = None
        if state["running"]:
            raise HTTPException(
                status_code=409,
                detail="Session already running. Stop it first.")
        period = req.period.strip()
        if not period:
            raise HTTPException(status_code=400,
                                detail="Period name is required")
        result = _sess.start_session(period)
        if not result["ok"]:
            raise HTTPException(status_code=500,
                                detail=result.get("error", "Start failed"))
        db.log_audit(_uname(user), "api_session_start", period)
        port = config.API_PORT
        return {
            "status":  "started",
            "period":  period,
            "stream":  f"/video_feed",
            "message": f"Camera session started for {period}",
        }

    @app.post("/api/session/stop")
    def api_session_stop(user: dict = Depends(teacher_required)):
        _sess.stop_session()
        db.log_audit(_uname(user), "api_session_stop",
                     _sess._SESSION_STATE.get("period", ""))
        return {"status": "stopped", "message": "Session stopped"}

    @app.get("/api/session/status")
    def api_session_status(_: dict = Depends(get_current_user)):
        state  = _sess.get_status()
        period = state.get("period")
        marked_rows: list = []
        if period:
            try:
                marked_rows = db.get_today_attendance(period) or []
            except Exception:
                pass
        already_marked = [
            {
                "student_id": r.get("student_id", ""),
                "name":       r.get("name", ""),
                "time":       str(r.get("time", ""))[:8],
                "confidence": int(float(r.get("confidence", 0)) * 100),
                "engine":     r.get("engine", ""),
            }
            for r in marked_rows
        ]
        total_students = 0
        try:
            total_students = len(db.get_all_students())
        except Exception:
            pass
        return {
            "running":        state.get("running", False),
            "period":         period,
            "started_at":     state.get("started_at"),
            "marked_count":   len(marked_rows),
            "total_students": total_students,
            "absent_count":   max(0, total_students - len(marked_rows)),
            "already_marked": already_marked,
            "error":          state.get("error") or "",
        }

    # ── Training ──────────────────────────────────────────────
    @app.post("/api/train")
    def api_train(background_tasks: BackgroundTasks,
                  user: dict = Depends(admin_required)):
        """
        Kicks off LBPH + dlib training in a background thread.
        Returns immediately; poll /api/train/status to check progress.
        """
        if _train_state["running"]:
            return {"status": "already_running",
                    "message": "Training is already in progress"}
        background_tasks.add_task(_run_training_bg)
        return {"status": "started",
                "message": "Training started in background. Poll /api/train/status"}

    @app.get("/api/train/status")
    def api_train_status(_: dict = Depends(get_current_user)):
        return {
            "running": _train_state["running"],
            "done":    _train_state["done"],
            "error":   _train_state["error"],
            "log":     _train_state["log"][-20:],   # last 20 lines
        }

    # =============================================================
    # TRAINING MANAGEMENT APIs  —  ADMIN ONLY
    # =============================================================

    # ── Strict admin-only dependency (NO hod, NO teacher, NO faculty) ─
    def admin_training_required(
            user: dict = Depends(get_current_user)) -> dict:
        """
        Strictly allow ONLY role=='admin'.
        HOD, faculty, teacher, student are all rejected with 403.
        """
        if user.get("role") != "admin":
            log.warning(
                "TRAINING ACCESS DENIED — role=%s sub=%s",
                user.get("role", "?"), user.get("sub", "?"))
            raise HTTPException(
                status_code=403,
                detail="Access denied: AI Training is restricted to Admin only.")
        return user

    # ── Selective training progress state (per-job) ───────────────────
    _selective_state: dict = {
        "running": False, "done": False, "error": "",
        "stage": "", "stages_done": [], "person_id": "", "role": "",
        "log": []
    }

    def _reset_selective_state():
        _selective_state.update({
            "running": False, "done": False, "error": "",
            "stage": "", "stages_done": [],
            "person_id": "", "role": "", "log": []
        })

    TRAIN_STAGES = [
        "reading_images",
        "processing_dataset",
        "augmentation",
        "training_model",
        "saving_model",
        "updating_trained_ids",
        "completed",
    ]

    def _selective_train_bg(role: str, person_id: str):
        """
        Background worker: calls train_selective.train_one_person()
        and updates _selective_state with stage progress.
        Only called for role=='admin' (validated at API layer).
        """
        import io, contextlib

        _selective_state["running"]     = True
        _selective_state["done"]        = False
        _selective_state["error"]       = ""
        _selective_state["stages_done"] = []
        _selective_state["stage"]       = "reading_images"
        _selective_state["person_id"]   = person_id
        _selective_state["role"]        = role
        _selective_state["log"]         = []

        def _advance(stage: str):
            if stage not in _selective_state["stages_done"]:
                _selective_state["stages_done"].append(stage)
            _selective_state["stage"] = stage
            _selective_state["log"].append(f"[{stage}] started")
            log.info("Selective training stage: %s — %s/%s", stage, person_id, role)

        try:
            from train_selective import (
                train_one_person,
                _get_dataset_dir,
                _load_trained_ids,
            )

            # ── Stage 1: Reading images ────────────────────────────
            _advance("reading_images")
            dataset_path = _get_dataset_dir(role, person_id)

            if not os.path.isdir(dataset_path):
                raise ValueError(
                    f"Dataset folder not found: data/dataset/{role}/{person_id}")

            imgs = [f for f in os.listdir(dataset_path)
                    if f.lower().endswith((".jpg", ".jpeg", ".png"))]
            if not imgs:
                raise ValueError(
                    f"No images found in dataset for {role}/{person_id}")

            _selective_state["log"].append(
                f"  Found {len(imgs)} raw images for {person_id}")

            # ── Stage 2: Processing dataset ────────────────────────
            _advance("processing_dataset")
            _selective_state["log"].append("  Preprocessing (resize + equalizeHist)...")

            # ── Stage 3: Augmentation ──────────────────────────────
            _advance("augmentation")
            _selective_state["log"].append("  Augmenting training data...")

            # ── Stage 4 + 5 + 6: Delegate to train_one_person() ───
            _advance("training_model")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ok = train_one_person(role, person_id)
            captured = buf.getvalue()
            _selective_state["log"].extend(
                [ln for ln in captured.split("\n") if ln.strip()])

            if not ok:
                raise RuntimeError(
                    f"train_one_person() returned False for {person_id}")

            # ── Stage 5: Saving model ──────────────────────────────
            _advance("saving_model")
            _selective_state["log"].append("  Model file saved to models/lbph_model.yml")

            # ── Stage 6: Update trained_ids ────────────────────────
            _advance("updating_trained_ids")
            _selective_state["log"].append("  trained_ids.json updated")

            # ── Stage 7: Completed ─────────────────────────────────
            _advance("completed")
            _selective_state["stages_done"] = TRAIN_STAGES[:]
            _selective_state["log"].append(
                f"  Training completed for {person_id} ({role})")
            _selective_state["done"] = True

        except Exception as exc:
            _selective_state["error"] = str(exc)
            _selective_state["done"]  = True
            _selective_state["log"].append(f"  ERROR: {exc}")
            log.error("Selective training error for %s/%s: %s",
                      role, person_id, exc)
        finally:
            _selective_state["running"] = False

    # ── Full-retraining background task ───────────────────────────────
    _full_train_state: dict = {
        "running": False, "done": False, "error": "", "log": []}

    def _full_retrain_bg():
        global _full_train_state
        _full_train_state = {
            "running": True, "done": False, "error": "", "log": []}
        try:
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                from train import train_all
                train_all()
            _full_train_state["log"]  = buf.getvalue().split("\n")
            _full_train_state["done"] = True
        except Exception as e:
            _full_train_state["error"] = str(e)
            _full_train_state["done"]  = True
        finally:
            _full_train_state["running"] = False

    # ── Helper: scan a dataset directory for enrolled IDs ─────────────
    def _scan_dataset(role: str) -> list:
        """
        Return list of dicts: {id, image_count}
        for all sub-folders that contain at least one image file.
        Also returns folders with ZERO images (image_count=0).
        """
        role_base = {
            "student": config.DATASET_DIR,
            "staff":   config.STAFF_DATASET_DIR,
            "hod":     config.HOD_DATASET_DIR,
        }.get(role, "")

        if not role_base or not os.path.isdir(role_base):
            return []

        prefix = "STU_" if role == "student" else None
        result = []
        for name in sorted(os.listdir(role_base)):
            if prefix and not name.upper().startswith(prefix.upper()):
                continue
            full = os.path.join(role_base, name)
            if not os.path.isdir(full):
                continue
            imgs = [f for f in os.listdir(full)
                    if f.lower().endswith((".jpg", ".jpeg", ".png"))]
            result.append({"id": name, "image_count": len(imgs)})
        return result

    # ── Helper: load trained_ids.json safely ──────────────────────────
    def _load_trained_ids_safe() -> dict:
        try:
            if os.path.exists(config.TRAINED_IDS_JSON):
                with open(config.TRAINED_IDS_JSON, "r") as f:
                    data = json.load(f)
                for k in ("students", "staff", "hod"):
                    if k not in data:
                        data[k] = []
                return data
        except Exception as e:
            log.warning("Could not read trained_ids.json: %s", e)
        return {"students": [], "staff": [], "hod": []}

    # ── GET /api/train/status/all ─────────────────────────────────────
    @app.get("/api/train/status/all")
    def api_train_status_all(
            user: dict = Depends(admin_training_required)):
        """
        Scan dataset folders and compare with trained_ids.json.
        Returns categorised trained / not_trained lists per role.
        ADMIN ONLY.
        """
        log.info("GET /api/train/status/all — admin=%s", user.get("sub"))

        registry = _load_trained_ids_safe()

        result = {}
        role_key_map = {
            "hod":     "hod",
            "staff":   "staff",
            "student": "students",
        }

        for role in ("hod", "staff", "student"):
            rkey    = role_key_map[role]
            trained = registry.get(rkey, [])
            all_ids = _scan_dataset(role)

            trained_list     = []
            not_trained_list = []

            for item in all_ids:
                pid  = item["id"]
                icount = item["image_count"]
                entry = {
                    "id":          pid,
                    "image_count": icount,
                    "trained":     pid in trained,
                }
                if pid in trained:
                    trained_list.append(entry)
                else:
                    not_trained_list.append(entry)

            result[role] = {
                "trained":     trained_list,
                "not_trained": not_trained_list,
            }

        return result

    # ── POST /api/train/selective ─────────────────────────────────────
    class SelectiveTrainReq(BaseModel):
        role: str           # "hod" | "staff" | "student"
        id:   str           # person_id

    @app.post("/api/train/selective")
    def api_train_selective(
            req: SelectiveTrainReq,
            background_tasks: BackgroundTasks,
            user: dict = Depends(admin_training_required)):
        """
        Selectively train a single person and append to the shared model.
        ADMIN ONLY.
        Validates role, ID, dataset folder, and image existence.
        """
        log.info(
            "POST /api/train/selective — admin=%s role=%s id=%s",
            user.get("sub"), req.role, req.id)

        # ── Validate role ──────────────────────────────────────────
        VALID_ROLES = ("hod", "staff", "student")
        if req.role not in VALID_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid role '{req.role}'. Must be one of: {VALID_ROLES}")

        # ── Validate ID format ─────────────────────────────────────
        clean_id = req.id.strip()
        if not clean_id or len(clean_id) > 64:
            raise HTTPException(
                status_code=400,
                detail="Invalid person ID (empty or too long)")

        # Students must use STU_ prefix — auto-apply if missing
        if req.role == "student" and not clean_id.upper().startswith("STU_"):
            clean_id = f"STU_{clean_id.upper()}"

        # ── Validate dataset folder ────────────────────────────────
        role_base = {
            "student": config.DATASET_DIR,
            "staff":   config.STAFF_DATASET_DIR,
            "hod":     config.HOD_DATASET_DIR,
        }[req.role]
        dataset_path = os.path.join(role_base, clean_id)

        if not os.path.isdir(dataset_path):
            raise HTTPException(
                status_code=404,
                detail=f"Dataset folder not found for {req.role}/{clean_id}")

        # ── Validate images exist ──────────────────────────────────
        imgs = [f for f in os.listdir(dataset_path)
                if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        if not imgs:
            raise HTTPException(
                status_code=422,
                detail=f"No images found for {clean_id} — enrol first")

        # ── Check if already running ───────────────────────────────
        if _selective_state.get("running"):
            return {
                "status":  "already_running",
                "message": "Another selective training is in progress",
            }

        # ── Check if already trained (allow re-train, just warn) ──
        registry  = _load_trained_ids_safe()
        rkey      = {"student": "students", "staff": "staff", "hod": "hod"}[req.role]
        is_update = clean_id in registry.get(rkey, [])

        # ── Start background training ──────────────────────────────
        _reset_selective_state()
        background_tasks.add_task(_selective_train_bg, req.role, clean_id)

        return {
            "status":      "started",
            "person_id":   clean_id,
            "role":        req.role,
            "image_count": len(imgs),
            "is_update":   is_update,
            "message":     (
                f"Updating existing model entry for {clean_id}"
                if is_update else
                f"Training new model entry for {clean_id}"
            ),
        }

    # ── GET /api/train/progress ───────────────────────────────────────
    @app.get("/api/train/progress")
    def api_train_progress(
            _: dict = Depends(admin_training_required)):
        """
        Poll endpoint for selective training progress.
        ADMIN ONLY.
        """
        return {
            "running":     _selective_state["running"],
            "done":        _selective_state["done"],
            "error":       _selective_state["error"],
            "stage":       _selective_state["stage"],
            "stages_done": _selective_state["stages_done"],
            "person_id":   _selective_state["person_id"],
            "role":        _selective_state["role"],
            "log":         _selective_state["log"][-30:],
        }

    # ── POST /api/train/full ──────────────────────────────────────────
    @app.post("/api/train/full")
    def api_train_full(
            background_tasks: BackgroundTasks,
            user: dict = Depends(admin_training_required)):
        """
        Full model retraining (all enrolled persons).
        ADMIN ONLY — requires explicit confirmation at frontend.
        """
        log.info(
            "POST /api/train/full — admin=%s", user.get("sub"))

        if _full_train_state.get("running"):
            return {
                "status":  "already_running",
                "message": "Full retraining already in progress",
            }
        if _selective_state.get("running"):
            return {
                "status":  "conflict",
                "message": "Selective training in progress — wait for it to finish",
            }
        background_tasks.add_task(_full_retrain_bg)
        return {
            "status":  "started",
            "message": "Full retraining started. Poll /api/train/status for progress.",
        }

    # ── GET /api/train/full/status ────────────────────────────────────
    @app.get("/api/train/full/status")
    def api_train_full_status(
            _: dict = Depends(admin_training_required)):
        """Full retraining progress. ADMIN ONLY."""
        return {
            "running": _full_train_state.get("running", False),
            "done":    _full_train_state.get("done",    False),
            "error":   _full_train_state.get("error",   ""),
            "log":     _full_train_state.get("log",     [])[-30:],
        }

    # ── Analytics ─────────────────────────────────────────────
    @app.get("/api/analytics/summary")
    def api_analytics_summary(_: dict = Depends(get_current_user)):
        """
        Returns a single object with all KPI numbers the
        EduTrack dashboard needs: total students, today present,
        avg attendance, engine stats, etc.
        """
        # ── Dashboard KPIs — uses get_dashboard_stats() for accurate counts ──
        # get_today_attendance() returns period-level rows, so len() overcounts.
        # get_dashboard_stats() uses COUNT(DISTINCT student_id) internally.
        stats          = db.get_dashboard_stats()
        total_members  = stats["total_members"]
        total_students = stats["total_students"]   # backward-compat (students only)
        present_today  = stats["present_today"]
        absent_today   = stats["absent_today"]
        pct_today      = stats["pct_today"]

        summary_rows = db.get_attendance_summary(30)
        engine_rows  = db.get_engine_stats(7)
        period_rows  = db.get_period_stats()

        # Avg from summary  (summary now always uses present_count / total_days)
        pcts = []
        for r in summary_rows:
            pc = r.get("present_count", 0)
            td = r.get("total_days",    1) or 1
            pcts.append(pc / td * 100)
        avg_att = round(sum(pcts) / len(pcts), 1) if pcts else 0

        # Critical students (<65%) — 65% of actual school days this semester
        critical = [r for r in summary_rows
                    if (r.get("present_count", 0) / (r.get("total_days", 1) or 1)) < 0.65]

        return {
            "total_members":   total_members,
            "total_students":  total_students,
            "present_today":   present_today,
            "absent_today":    absent_today,
            "pct_today":       pct_today,
            "avg_attendance":  avg_att,
            "critical_count":  len(critical),
            "critical_students": [dict(r) for r in critical],
            "engine_stats":    [dict(r) for r in engine_rows],
            "period_stats":    [dict(r) for r in period_rows],
        }

    @app.get("/api/analytics/engine")
    def api_engine_stats(days: int = 7,
                         _: dict = Depends(get_current_user)):
        return [dict(r) for r in db.get_engine_stats(days)]

    @app.get("/api/analytics/period")
    def api_period_stats(_: dict = Depends(get_current_user)):
        return [dict(r) for r in db.get_period_stats()]

    @app.get("/api/analytics/twins")
    def api_twin_log(days: int = 7,
                     _: dict = Depends(get_current_user)):
        return [dict(r) for r in db.get_twin_analysis_log(days)]

    # ── Timetable ─────────────────────────────────────────────
    @app.get("/api/timetable")
    def api_timetable(_: dict = Depends(get_current_user)):
        rows = db.get_timetable()
        return [dict(r) for r in rows]

    # ── Settings ──────────────────────────────────────────────
    @app.get("/api/settings")
    def api_get_settings(_: dict = Depends(get_current_user)):
        return {
            "LBPH_THRESHOLD":          config.LBPH_THRESHOLD,
            "DLIB_DISTANCE":           config.DLIB_DISTANCE,
            "MIN_CONFIDENCE_PCT":      config.MIN_CONFIDENCE_PCT,
            "CONFIRM_FRAMES_REQUIRED": config.CONFIRM_FRAMES_REQUIRED,
            "LIVENESS_THRESHOLD":      config.LIVENESS_THRESHOLD,
            "LIVENESS_ON":             config.LIVENESS_ON,
            "CAMERA_INDEX":            config.CAMERA_INDEX,
        }

    @app.post("/api/settings")
    def api_save_settings(data: dict,
                          user: dict = Depends(admin_required)):
        for key, cast in {
            "LBPH_THRESHOLD": float,
            "DLIB_DISTANCE": float,
            "MIN_CONFIDENCE_PCT": float,
            "CONFIRM_FRAMES_REQUIRED": int,
            "LIVENESS_THRESHOLD": float,
            "LIVENESS_ON": bool,
            "CAMERA_INDEX": int,
        }.items():
            if key in data:
                setattr(config, key, cast(data[key]))
        return {"status": "ok", "message": "Settings updated"}

    # ── Export ────────────────────────────────────────────────
    # ── Override Log (new) ────────────────────────────────────
    @app.get("/api/override/log")
    def api_override_log(limit: int = 200,
                         _: dict = Depends(get_current_user)):
        """Return override history with who/student/date/time details."""
        try:
            rows = db.get_override_log(limit)
            return rows
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Alert: send mail for one student ─────────────────────
    @app.post("/api/alerts/send-mail")
    def api_send_alert_mail(data: dict,
                            user: dict = Depends(teacher_required)):
        """
        Send a real-time HTML Gmail alert to student + parent (attendance < 75%).
        Uses email_alerts module with retry logic and HTML template.
        data = { student_id, name, roll, pct, section, dept,
                 student_email, parent_email }
        """
        from email_alerts import send_low_attendance_alert

        student_id = data.get("student_id", "")
        pct        = float(data.get("pct", 0))

        # Build student dict matching email_alerts expected format
        student = {
            "student_id":            student_id,
            "name":                  data.get("name", "Student"),
            "roll_number":           data.get("roll", ""),
            "dept":                  data.get("dept", ""),
            "section":               data.get("section", ""),
            "attendance_percentage": pct,
            "student_email":         data.get("student_email", ""),
            "parent_email":          data.get("parent_email", ""),
        }

        result = send_low_attendance_alert(student)

        # Log the action
        db.log_audit(
            _uname(user), "alert_mail_sent",
            student_id,
            f"pct={pct}% sent_to={','.join(result['sent_to']) or 'none'} "
            f"errors={len(result['errors'])}",
            ""
        )

        return {
            "status":  "sent" if result["sent"] else ("skipped" if not result["errors"] else "failed"),
            "sent_to": result["sent_to"],
            "errors":  result["errors"],
            "message": result["message"],
        }

    # ── Alert: auto-send all below 75% ───────────────────────
    @app.post("/api/alerts/auto-send")
    def api_auto_send_alerts(user: dict = Depends(teacher_required)):
        """
        Auto-send real-time HTML Gmail alerts to ALL students with attendance < 75%.
        Uses email_alerts module with retry logic and HTML template.
        """
        from email_alerts import send_bulk_alerts

        try:
            low_students = db.get_low_attendance_students(75.0, 30)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        summary = send_bulk_alerts(low_students)

        # Log each student action
        for r in summary["results"]:
            if r["status"] not in ("skipped",):
                db.log_audit(
                    _uname(user), "auto_alert_sent",
                    r["student_id"],
                    f"pct={r['pct']}% sent_to={','.join(r['sent_to']) or 'none'} "
                    f"errors={len(r['errors'])}",
                    ""
                )

        return {
            "status":      "done",
            "count":       summary["total"],
            "emails_sent": summary["emails_sent"],
            "skipped":     summary["skipped"],
            "failed":      summary["failed"],
            "results":     summary["results"],
            "message":     summary["message"],
        }

    # ── Alert: test Gmail connection ──────────────────────────
    @app.get("/api/alerts/test-gmail")
    def api_test_gmail(user: dict = Depends(teacher_required)):
        """
        Test Gmail SMTP connection without sending any email.
        Returns ok=True if credentials in .env are working.
        """
        from email_alerts import test_gmail_connection
        return test_gmail_connection()

    # ── Low attendance list (for alerts page) ────────────────
    @app.get("/api/alerts/low-attendance")
    def api_low_attendance(threshold: float = 75.0, days: int = 180,
                           _: dict = Depends(get_current_user)):
        """Return all students with attendance below threshold, grouped for display."""
        try:
            rows = db.get_low_attendance_students(threshold, days)
            return rows
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))


    # ── Face Image Upload (Browser Enrollment) ────────────────
    @app.post("/api/enroll/face-images")
    async def api_enroll_face_images(
        request: Request,
        user: dict = Depends(teacher_required),
    ):
        """
        Accepts multipart/form-data with:
          entity_id : str  — student_id / staff_id / hod_id
          role      : str  — "student" | "faculty" | "hod"
          files     : List[UploadFile] — JPEG images from browser webcam
        Saves images to data/known_faces/<entity_id>/ (students)
                      or data/known_faces/staff/<entity_id>/ (faculty)
                      or data/known_faces/hod/<entity_id>/ (hod)
        """
        import shutil
        try:
            from fastapi import UploadFile, File, Form
        except ImportError:
            raise HTTPException(status_code=500, detail="UploadFile not available")

        form_data  = await request.form()
        entity_id  = str(form_data.get("entity_id", "")).strip()
        role       = str(form_data.get("role", "student")).strip().lower()
        file_items = form_data.getlist("files")

        if not entity_id:
            raise HTTPException(status_code=422, detail="entity_id is required")
        if not file_items:
            raise HTTPException(status_code=422, detail="No files uploaded")

        # Determine save directory based on role
        if role in ("faculty", "staff", "classincharge"):
            save_dir = os.path.join(config.BASE_DIR, "data", "known_faces", "staff", entity_id)
            dataset_dir = os.path.join(config.BASE_DIR, "data", "dataset", "staff", entity_id)
        elif role == "hod":
            save_dir = os.path.join(config.BASE_DIR, "data", "known_faces", "hod", entity_id)
            dataset_dir = os.path.join(config.BASE_DIR, "data", "dataset", "hod", entity_id)
        else:
            save_dir = os.path.join(config.BASE_DIR, "data", "known_faces", entity_id)
            dataset_dir = os.path.join(config.BASE_DIR, "data", "dataset", entity_id)

        os.makedirs(save_dir,    exist_ok=True)
        os.makedirs(dataset_dir, exist_ok=True)

        # Find the next available index (don't overwrite existing images)
        existing = [f for f in os.listdir(save_dir) if f.lower().endswith('.jpg')]
        start_idx = len(existing)

        saved = 0
        for i, file_item in enumerate(file_items):
            try:
                contents = await file_item.read()
                if not contents:
                    continue
                idx      = start_idx + i
                filename = f"{entity_id}_p{idx:04d}.jpg"
                dest_kf  = os.path.join(save_dir, filename)
                dest_ds  = os.path.join(dataset_dir, filename)
                with open(dest_kf, "wb") as fp:
                    fp.write(contents)
                # Mirror to dataset dir (used for training)
                shutil.copy2(dest_kf, dest_ds)
                saved += 1
            except Exception as img_err:
                log.warning("Face image save error idx=%d: %s", i, img_err)

        if saved == 0:
            raise HTTPException(status_code=400,
                                detail="No images could be saved — check file format")

        db.log_audit(
            _uname(user), "enroll_face_images", entity_id,
            f"{saved} images saved (role={role})",
            request.client.host if request.client else "?",
        )
        return {
            "status":    "saved",
            "entity_id": entity_id,
            "role":      role,
            "count":     saved,
            "save_dir":  save_dir,
            "message":   f"{saved} face images saved for {entity_id}. Run training to activate.",
        }

    # ── Export CSV ────────────────────────────────────────────
    @app.get("/api/export/csv")
    def api_export_csv(period: str = None,
                       _: dict = Depends(get_current_user)):
        import csv, io
        today_str = datetime.now().strftime("%Y-%m-%d")
        rows      = db.get_today_attendance(period)
        out       = io.StringIO()
        w = csv.DictWriter(out, fieldnames=[
            "name", "student_id", "period", "date",
            "time", "confidence", "engine"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
        out.seek(0)
        return StreamingResponse(
            iter([out.read()]),
            media_type="text/csv",
            headers={"Content-Disposition":
                     f"attachment; filename=attendance_{today_str}.csv"})

    # ── Frontend static files ─────────────────────────────────
    frontend_dir = os.path.join(config.BASE_DIR, "frontend")
    if os.path.isdir(frontend_dir):
        # Serve frontend/style.css and frontend/app.js at /style.css, /app.js
        @app.get("/style.css")
        def serve_css():
            p = os.path.join(frontend_dir, "style.css")
            return FileResponse(p, media_type="text/css") if os.path.exists(p) else HTMLResponse("", 404)

        @app.get("/app.js")
        def serve_js():
            p = os.path.join(frontend_dir, "app.js")
            if not os.path.exists(p): return HTMLResponse("", 404)
            return FileResponse(p, media_type="application/javascript",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate",
                         "Pragma": "no-cache", "Expires": "0"})

        @app.get("/features.js")
        def serve_features_js():
            p = os.path.join(frontend_dir, "features.js")
            if not os.path.exists(p): return HTMLResponse("", 404)
            return FileResponse(p, media_type="application/javascript",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate",
                         "Pragma": "no-cache", "Expires": "0"})

        @app.get("/features.css")
        def serve_features_css():
            p = os.path.join(frontend_dir, "features.css")
            if not os.path.exists(p): return HTMLResponse("", 404)
            return FileResponse(p, media_type="text/css",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate",
                         "Pragma": "no-cache", "Expires": "0"})

        @app.get("/enroll_roles.js")
        def serve_enroll_roles_js():
            p = os.path.join(frontend_dir, "enroll_roles.js")
            return FileResponse(p, media_type="application/javascript") if os.path.exists(p) else HTMLResponse("", 404)

        @app.get("/edudrill.js")
        def serve_edudrill_js():
            p = os.path.join(frontend_dir, "edudrill.js")
            return FileResponse(p, media_type="application/javascript") if os.path.exists(p) else HTMLResponse("", 404)

        @app.get("/edudrill.css")
        def serve_edudrill_css():
            p = os.path.join(frontend_dir, "edudrill.css")
            return FileResponse(p, media_type="text/css") if os.path.exists(p) else HTMLResponse("", 404)

        @app.get("/attendance.html", response_class=HTMLResponse)
        @app.get("/attendance", response_class=HTMLResponse)
        def serve_attendance_html():
            p = os.path.join(frontend_dir, "attendance.html")
            return FileResponse(p) if os.path.exists(p) else HTMLResponse("<h2>attendance.html not found</h2>", 404)

        @app.get("/attendance.css")
        def serve_attendance_css():
            p = os.path.join(frontend_dir, "attendance.css")
            return FileResponse(p, media_type="text/css") if os.path.exists(p) else HTMLResponse("", 404)

        @app.get("/attendance.js")
        def serve_attendance_js():
            p = os.path.join(frontend_dir, "attendance.js")
            return FileResponse(p, media_type="application/javascript") if os.path.exists(p) else HTMLResponse("", 404)

        @app.get("/app", response_class=HTMLResponse)
        @app.get("/", response_class=HTMLResponse)
        def frontend():
            idx = os.path.join(frontend_dir, "index.html")
            if os.path.exists(idx):
                return FileResponse(idx)
            return HTMLResponse(
                "<h2>Frontend not found</h2>"
                f"<p>Expected: {idx}</p>"
                "<p>Place index.html, style.css, app.js inside the "
                "<code>frontend/</code> folder.</p>")

    # ── Extra Extensions (courses, electives, timetable v2, enrollment) ──
    # MUST register BEFORE Feature routes so /api/faculty/all (specific)
    # is matched before /api/faculty/{fac_id} (wildcard). FastAPI matches
    # routes top-down — registering the wildcard first causes "all" to be
    # treated as a fac_id and return "Faculty not found".
    try:
        from api_extras import register_extra_routes
        register_extra_routes(app, get_current_user,
                              teacher_required, admin_required)
        log.info("Extra routes registered (courses, electives, timetable).")
    except Exception as _xe:
        log.warning("Extra routes not loaded: %s", _xe)

    # ── Feature Extensions (departments drill-down + faculty mgmt) ──
    try:
        from api_features import register_feature_routes
        register_feature_routes(app, get_current_user,
                                teacher_required, admin_required)
        log.info("Feature routes registered (departments + faculty).")
    except Exception as _fe:
        log.warning("Feature routes not loaded: %s", _fe)

    # ── F-07: Authenticated faculty lookup (safety-net registration) ──────────
    # GET /api/faculty/{fac_id} requires a valid JWT.
    # The full faculty record (including email, mobile, etc.) must never be
    # returned to unauthenticated callers.
    #
    # api_features.py already registers this route with Depends(get_current_user).
    # This block is a defence-in-depth fallback: if api_features fails to load,
    # this declaration ensures the endpoint still exists and still enforces auth.
    # FastAPI uses the first matching registration (api_features wins when loaded).
    @app.get("/api/faculty/{fac_id}", tags=["Faculty"])
    def api_faculty_detail_secure(
        fac_id: str,
        _: dict = Depends(get_current_user),
    ):
        """
        F-07 security fix: full faculty profile — requires valid JWT.
        Admin/HOD panels use this to retrieve complete profile data.
        Returns HTTP 401 if no valid token is present.
        """
        fac = db.get_faculty_by_id(fac_id.strip().upper())
        if not fac:
            raise HTTPException(status_code=404, detail="Faculty not found")
        return fac

    # ── Override Feature (attendance override with permission control) ──
    try:
        from api_override import register_override_routes
        register_override_routes(app, get_current_user,
                                 teacher_required, admin_required, _uname)
        log.info("Override routes registered (attendance overrides).")
    except Exception as _ov:
        log.warning("Override routes not loaded: %s", _ov)

    # ── HOD Management (admin manages HODs, HODs manage staff) ──
    try:
        from api_hod import register_hod_routes
        register_hod_routes(app, get_current_user, admin_required, _uname)
        log.info("HOD management routes registered.")
    except Exception as _he:
        log.warning("HOD routes not loaded: %s", _he)

    # ── Role-Based Attendance (public — no auth needed) ─────────
    try:
        from api_role_attendance import register_role_attendance_routes
        register_role_attendance_routes(app, db, config)
        log.info("Role attendance routes registered.")
    except Exception as _re:
        log.warning("Role attendance routes not loaded: %s", _re)

    # ── E Auth Merge v9.7 — rate limiter + OTP + user routes ─────
    # _limiter was already instantiated at the top of create_app(); here we
    # just wire it into app.state and register the middleware + error handler.
    if SLOWAPI_OK:
        try:
            app.state.limiter = _limiter

            @app.exception_handler(RateLimitExceeded)
            async def _rate_limit_handler(request, exc):
                path = str(request.url.path)
                if "forgot-password" in path:
                    detail = "Too many OTP requests. Please wait 5 minutes and try again."
                else:
                    detail = "Too many requests. Please wait 1 minute and try again."
                return JSONResponse(
                    status_code=429,
                    content={"detail": detail, "error": "rate_limit_exceeded"},
                )
            app.add_middleware(SlowAPIMiddleware)
            log.info("slowapi rate limiting enabled.")
        except Exception as _sl_err:
            log.warning("slowapi middleware failed: %s", _sl_err)

    if AUTH_MERGE_OK:
        # Bootstrap OTP verifications table in attendance.db
        try:
            init_otp_table()
            log.info("OTP table ready.")
        except Exception as _otp_err:
            log.warning("OTP table init error: %s", _otp_err)

        # Register forgot-password / verify-otp / reset-password / change-password
        try:
            app.include_router(_auth_ext_router, prefix="/auth",
                               tags=["Auth — Password Reset"])
            log.info("Auth extension routes registered (/auth/forgot-password etc.).")
        except Exception as _ar_err:
            log.warning("Auth extension routes failed: %s", _ar_err)

        # Register admin user management CRUD
        try:
            app.include_router(_user_router, prefix="/user",
                               tags=["User Management"])
            log.info("User management routes registered (/user/).")
        except Exception as _ur_err:
            log.warning("User management routes failed: %s", _ur_err)

        # Convenience /api/auth/* aliases (frontend calls these)
        try:
            from auth_routes import (
                ForgotPasswordRequest, forgot_password,
                VerifyOTPRequest,     verify_otp,
                ResetPasswordRequest, reset_password,
            )
            from pydantic import ValidationError as _PydanticValidationError

            def _pydantic_to_http(exc: "_PydanticValidationError") -> "HTTPException":
                """Convert a Pydantic v2 ValidationError into an HTTPException 400.

                When a @validator raises ValueError (e.g. the password blocklist
                check in auth_utils.validate_strong_password), Pydantic wraps it
                in a ValidationError.  If that reaches the ASGI middleware stack
                uncaught it crashes the security-headers middleware and produces a
                "Network error" in the browser instead of a clean 400 response.

                This helper extracts the first human-readable message and returns
                an HTTPException that FastAPI can serialise normally.
                """
                msgs = []
                for err in exc.errors():
                    msg = err.get("msg", "")
                    # Pydantic v2 prefixes validator messages with "Value error, "
                    if msg.startswith("Value error, "):
                        msg = msg[len("Value error, "):]
                    if msg:
                        msgs.append(msg)
                detail = msgs[0] if msgs else "Invalid input."
                return HTTPException(status_code=400, detail=detail)

            @app.post("/api/auth/forgot-password", tags=["Auth — Password Reset"])
            @_limiter.limit("3/5minutes") if SLOWAPI_OK else lambda f: f
            async def api_forgot_password(request: Request):
                body = await request.json()
                try:
                    return forgot_password(request, ForgotPasswordRequest(**body))
                except _PydanticValidationError as _pve:
                    raise _pydantic_to_http(_pve)

            @app.post("/api/auth/verify-otp", tags=["Auth — Password Reset"])
            async def api_verify_otp_alias(request: Request):
                body = await request.json()
                try:
                    return verify_otp(VerifyOTPRequest(**body))
                except _PydanticValidationError as _pve:
                    raise _pydantic_to_http(_pve)

            @app.post("/api/auth/reset-password", tags=["Auth — Password Reset"])
            async def api_reset_password_alias(request: Request):
                body = await request.json()
                try:
                    return reset_password(ResetPasswordRequest(**body))
                except _PydanticValidationError as _pve:
                    raise _pydantic_to_http(_pve)

            @app.get("/api/auth/me", tags=["Auth — Password Reset"])
            def api_me(user: dict = Depends(get_current_user)):
                return {
                    "sub":      user.get("sub"),
                    "role":     user.get("role"),
                    "username": user.get("sub"),
                }

            # Serve OTP patch JS from frontend dir
            @app.get("/frontend_otp_patch.js")
            def serve_otp_js():
                p = os.path.join(config.BASE_DIR, "frontend", "frontend_otp_patch.js")
                return FileResponse(p, media_type="application/javascript") \
                    if os.path.exists(p) else HTMLResponse("", 404)

            # ── F-07: Public email-hint endpoint (no auth required) ───────────
            # Used by the forgot-password flow in frontend_otp_patch.js to
            # resolve a Faculty ID to a masked email address BEFORE the user
            # has a valid JWT.
            #
            # FRONTEND NOTE: frontend_otp_patch.js should call
            #   GET /api/faculty/{fac_id}/email-hint
            # instead of GET /api/faculty/{fac_id} for the forgot-password flow.
            # The full /api/faculty/{fac_id} endpoint now requires a valid JWT
            # (F-07 fix) and will return HTTP 401 for unauthenticated callers.
            @app.get("/api/faculty/{fac_id}/email-hint", tags=["Faculty"])
            @(_limiter.limit("10/minute") if SLOWAPI_OK else lambda f: f)
            def api_faculty_email_hint(fac_id: str, request: Request):
                """
                F-07 security fix: public endpoint that returns ONLY a masked
                email address for a given Faculty ID.

                Mask format: first 2 chars of local part + *** + @domain.
                  e.g.  faculty@college.edu  →  fa***@college.edu

                Rate-limited to 10 requests/minute per IP to prevent
                fac_id enumeration via email harvesting.

                Returns:
                  {"found": true,  "email_hint": "fa***@college.edu"}
                  {"found": false, "email_hint": null}
                """
                fac = db.get_faculty_by_id(fac_id.strip().upper())
                if not fac:
                    return {"found": False, "email_hint": None}

                raw_email = (fac.get("email") or "").strip()
                if not raw_email or "@" not in raw_email:
                    # Faculty exists but has no email on file
                    return {"found": True, "email_hint": None}

                local, domain = raw_email.rsplit("@", 1)
                masked_local  = local[:2] + "***" if len(local) > 2 else local[:1] + "***"
                return {"found": True, "email_hint": f"{masked_local}@{domain}"}

            log.info("/api/auth/* convenience aliases registered.")
            log.info("F-07: /api/faculty/<id>/email-hint (public, rate-limited) registered.")
        except Exception as _alias_err:
            log.warning("/api/auth/* aliases failed: %s", _alias_err)
    else:
        log.warning("E auth merge not active — run: pip install -r requirements.txt")
    # ─────────────────────────────────────────────────────────────

    return app


# =============================================================
# ENTRY POINT  (called from main.py option 4)
# =============================================================
def run_api():
    if not FASTAPI_OK:
        print("  ERROR: pip install fastapi uvicorn")
        return

    app = create_app()

    # ── F-02: one-time bcrypt migration (plain-text → bcrypt) ─
    # Runs before the server accepts requests.  Already-hashed rows are
    # skipped instantly; only plain-text rows are updated.
    try:
        _migrated = db.migrate_plaintext_passwords()
        if _migrated:
            log.info("F-02 startup migration: %d password(s) upgraded to bcrypt.", _migrated)
    except Exception as _mig_err:
        log.warning("F-02 startup migration failed (non-fatal): %s", _mig_err)

    # ── Startup route-registration sanity check ───────────────
    # Verifies that the enroll OTP routes are actually registered.
    # If missing, it means create_app() silently failed partway through —
    # this produces a clear console error instead of mysterious 404s.
    _registered_paths = {r.path for r in app.routes}
    _required_routes  = [
        "/api/enroll/send-otp",
        "/api/enroll/verify-otp",
        "/api/enroll/face-images",
        "/api/login",
        "/api/students",
    ]
    _missing = [r for r in _required_routes if r not in _registered_paths]
    if _missing:
        log.error(
            "STARTUP ERROR: The following API routes failed to register: %s  "
            "This causes 404 errors in the browser. "
            "Check for import errors above and restart the server.",
            _missing,
        )
        print(f"\n  ⚠  STARTUP WARNING: {len(_missing)} route(s) not registered: "
              f"{_missing}\n  Check server logs for import errors.\n")
    else:
        log.info("Route registration OK — all required endpoints present.")

    import socket
    port = config.API_PORT
    for p in [port, port+1, port+2, 8080, 8888, 9000]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", p))
            port = p
            break
        except OSError:
            continue
    else:
        print("  ERROR: No free port. Kill old server and retry.")
        return

    if port != config.API_PORT:
        print(f"  [INFO] Port {config.API_PORT} busy — using {port}")

    print(f"\n  ┌──────────────────────────────────────────────────────┐")
    print(f"  │  EduTrack Pro  ·  Smart Attendance System  v9.6      │")
    print(f"  │                                                        │")
    print(f"  │  Dashboard  : http://localhost:{port}/app             │")
    print(f"  │  API Docs   : http://localhost:{port}/docs            │")
    print(f"  │  Video Feed : http://localhost:{port}/video_feed      │")
    print(f"  │                                                        │")
    print(f"  │  Admin  (ADMIN))    : admin   / Admin@123          │")
    print(f"  │  HOD    (HOD) : HOD001  / Hod@123            │")
    print(f"  │  Staff  (STAF)       : FAC001  / Staff@123          │")
    print(f"  │  Press Ctrl+C to stop                                 │")
    print(f"  └──────────────────────────────────────────────────────┘\n")

    try:
        uvicorn.run(app, host=config.API_HOST, port=port,
                    log_level="warning")
    except OSError as e:
        print(f"\n  ERROR: {e}")
        print(f"  Kill the process using port {port} and retry.")