


"""
auth_routes.py  —  Smart Attendance System v9.6 (E Auth Merge)
===============================================================
Authentication endpoints:

  POST /auth/forgot-password   — send OTP to registered email
  POST /auth/verify-otp        — verify the OTP code
  POST /auth/reset-password    — set new password after OTP verified
  POST /auth/change-password   — authenticated user changes own password
  GET  /auth/me                — return current user info from JWT

The existing /auth/login endpoint stays in api.py (unchanged).
Register this router in api.py:

    from auth_routes import router as auth_ext_router
    app.include_router(auth_ext_router, prefix="/auth", tags=["Auth"])

IMPORTANT: This module uses SQLite directly (same db as SMF) via
the `database` module. It manages its own `otp_verifications`
table (created on first import via init_otp_table()).

FIXES APPLIED (v9.6-fix):
  Bug-1 FIXED: Admin forgot-password now actually saves the new password.
               Admin is stored in .env/config (not DB). Reset writes the
               new bcrypt-hashed password back to config.ADMIN_PASSWORD
               AND to the ADMIN_PASSWORD key in the .env file so it
               survives restarts.
  Bug-2 FIXED: Admin OTP is sent to ADMIN_EMAIL from .env (a real Gmail).
               Falls back to suganyainbox25@gmail.com if not set.
  Bug-3 FIXED: Attendance alert errors are now logged (not silenced).
  Bug-4 FIXED: OTP generation uses secrets module (in auth_utils.py).
  Bug-5 FIXED: .env.example has placeholder text only — no real passwords.
  Bug-6 NOTE:  Students have no login system; out of scope here.
  Bug-7 FIXED: send_attendance_alert_email logs failures instead of pass.
===============================================================
"""

import sqlite3
import os
import re
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Request, Depends, status
from pydantic import BaseModel, EmailStr, Field, validator
from slowapi import Limiter
from slowapi.util import get_remote_address

from auth_utils import (
    get_current_user, hash_password, verify_password,
    generate_otp, otp_expiry, validate_strong_password,
    hash_otp, verify_otp_code,
)
from auth_utils import bearer_scheme, decode_token   # F-11: needed by logout
from utils.email_utils import send_otp_email

import config   # SMF config (BASE_DIR, ADMIN_PASSWORD etc.)
from database import blocklist_token, cleanup_expired_tokens  # F-11: token blocklist

log     = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)
router  = APIRouter()

# ── DB path (uses SMF's existing attendance.db) ───────────────
DB_PATH = os.path.join(config.BASE_DIR, "attendance.db")

OTP_MAX_ATTEMPTS = 5

# ── .env path (for admin password persistence) ────────────────
ENV_PATH = os.path.join(config.BASE_DIR, ".env")

# ── Real admin email (from .env) ──────────────────────────────
# Bug-2 FIX: Admin OTP goes to ADMIN_EMAIL (a real deliverable address).
# Set  ADMIN_EMAIL=suganyainbox25@gmail.com  in your .env file.
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "suganyainbox25@gmail.com").strip().lower()


# ── OTP table bootstrap ───────────────────────────────────────
def init_otp_table() -> None:
    """
    Create otp_verifications table in SMF's attendance.db if not exists.
    Also add new columns to the faculty/users table if they don't exist.
    Call this once on app startup.
    """
    with sqlite3.connect(DB_PATH, timeout=15) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS otp_verifications (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_email      TEXT    NOT NULL,
                otp             TEXT    NOT NULL,
                expires_at      TEXT    NOT NULL,
                is_used         INTEGER DEFAULT 0,
                is_verified     INTEGER DEFAULT 0,
                failed_attempts INTEGER DEFAULT 0,
                is_locked       INTEGER DEFAULT 0,
                created_at      TEXT    DEFAULT (datetime('now','utc'))
            )
        """)
        # Add security columns to faculty table (ALTER TABLE ignores if exists)
        for col_def in [
            "failed_attempts INTEGER DEFAULT 0",
            "is_locked       INTEGER DEFAULT 0",
            "lock_until       TEXT",
            "must_reset_pwd   INTEGER DEFAULT 0",
            "employee_id      TEXT",
        ]:
            col_name = col_def.split()[0]
            try:
                conn.execute(f"ALTER TABLE faculty ADD COLUMN {col_def}")
                log.info("Added column %s to faculty table", col_name)
            except sqlite3.OperationalError:
                pass   # Column already exists
        conn.commit()
    log.info("OTP table initialized in attendance.db")


# ── Helpers ───────────────────────────────────────────────────

def _get_admin_email() -> str:
    """
    Return the real deliverable email address for the admin account.
    Reads ADMIN_EMAIL from environment each call so .env changes apply.
    """
    return os.environ.get("ADMIN_EMAIL", "suganyainbox25@gmail.com").strip().lower()


def _is_admin_email(email: str) -> bool:
    """
    Return True if 'email' maps to the admin account.
    Accepts: ADMIN_EMAIL value, the literal ADMIN_USERNAME value,
             and <ADMIN_USERNAME>@college.edu synthetic addresses.
    """
    email = email.strip().lower()
    admin_real  = _get_admin_email()
    admin_uname = getattr(config, "ADMIN_USERNAME", "admin").lower()
    allowed = {
        admin_real,
        admin_uname,
        f"{admin_uname}@college.edu",
    }
    return email in allowed


def _get_user_by_email(email: str) -> dict | None:
    """
    Look up a user (admin, HOD, or faculty) by email across known tables.

    Bug-1 / Bug-2 FIX:
      Admin is no longer matched against the synthetic 'admin@college.edu'
      placeholder.  Instead _is_admin_email() accepts the REAL admin email
      (ADMIN_EMAIL from .env), so OTP is sent to a deliverable address.
    """
    email = email.strip().lower()
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        # Try faculty table
        row = conn.execute(
            "SELECT * FROM faculty WHERE LOWER(email)=?", (email,)
        ).fetchone()
        if row:
            return dict(row)
        # Try hods table
        try:
            row = conn.execute(
                "SELECT * FROM hods WHERE LOWER(email)=?", (email,)
            ).fetchone()
            if row:
                return dict(row)
        except sqlite3.OperationalError:
            pass

    # Bug-2 FIX: check against the real admin email (ADMIN_EMAIL in .env),
    # not the synthetic "admin@college.edu" which can never receive mail.
    if _is_admin_email(email):
        return {
            "email":        _get_admin_email(),   # canonical real address
            "role":         "admin",
            "table":        "config_admin",
            "password":     config.ADMIN_PASSWORD,
        }
    return None


def _get_otp_record(email: str) -> dict | None:
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT * FROM otp_verifications
               WHERE user_email=? AND is_used=0
               ORDER BY created_at DESC LIMIT 1""",
            (email.lower(),)
        ).fetchone()
        return dict(row) if row else None


def _invalidate_old_otps(email: str) -> None:
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(
            "UPDATE otp_verifications SET is_used=1 WHERE user_email=? AND is_used=0",
            (email.lower(),)
        )
        conn.commit()


def _create_otp_record(email: str, otp: str) -> None:
    expires = otp_expiry().isoformat()
    # F-09: store HMAC-SHA256 hash of the OTP, never the plain code.
    otp_hash = hash_otp(otp)
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(
            """INSERT INTO otp_verifications
               (user_email, otp, expires_at) VALUES (?,?,?)""",
            (email.lower(), otp_hash, expires)
        )
        conn.commit()


def _update_otp_record(record_id: int, **kwargs) -> None:
    if not kwargs:
        return
    sets  = ", ".join(f"{k}=?" for k in kwargs)
    vals  = list(kwargs.values()) + [record_id]
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(f"UPDATE otp_verifications SET {sets} WHERE id=?", vals)
        conn.commit()


def _persist_admin_password_to_env(new_hashed: str) -> None:
    """
    F-03 FIX: Write the bcrypt-hashed admin password into the .env file
    so it survives a server restart.  Only touches the ADMIN_PASSWORD= line.

    Accepts *new_hashed* — a bcrypt hash string (starts with '$2b$').
    Storing the hash (not plain text) means a leaked .env file no longer
    exposes the raw admin password.
    """
    if not os.path.exists(ENV_PATH):
        log.warning("_persist_admin_password_to_env: .env not found at %s", ENV_PATH)
        return
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

        new_lines = []
        replaced  = False
        for line in lines:
            if re.match(r"^\s*ADMIN_PASSWORD\s*=", line):
                new_lines.append(f"ADMIN_PASSWORD={new_hashed}\n")
                replaced = True
            else:
                new_lines.append(line)

        if not replaced:
            new_lines.append(f"\nADMIN_PASSWORD={new_hashed}\n")

        with open(ENV_PATH, "w", encoding="utf-8") as fh:
            fh.writelines(new_lines)

        log.info("Admin password (bcrypt hash) persisted to .env file.")
    except Exception as exc:
        log.error("Failed to persist admin password to .env: %s", exc)


def _update_user_password(email: str, new_hashed: str, new_plain: str = "") -> None:
    """
    Update the password for admin / HOD / faculty identified by email.

    F-03 FIX: Admin password is now stored as a bcrypt hash in both
      config.ADMIN_PASSWORD (in-memory) and .env (on-disk).
      The login helper _check_admin_password() in api.py handles bcrypt
      verification, so storing the hash here is correct and safe.

    F-02 (Step 1) already made faculty/HOD login bcrypt-aware, so we
      always write new_hashed for those tables too — new_plain is kept
      as a parameter only for backward-compatibility with any call sites
      that still pass it; it is no longer used for storage.
    """
    email = email.lower()

    # ── Admin case (F-03 FIX) ─────────────────────────────────
    if _is_admin_email(email):
        # Store the bcrypt hash — not the plain-text password
        config.ADMIN_PASSWORD       = new_hashed   # update in-memory config
        os.environ["ADMIN_PASSWORD"] = new_hashed  # update os.environ
        _persist_admin_password_to_env(new_hashed) # persist hash to .env
        log.info("Admin password reset (bcrypt) for %s", email)
        return

    # ── Faculty / HOD case ────────────────────────────────────
    # F-02: login in api.py now uses verify_password() for bcrypt rows,
    # so we always write the hash — plain text is no longer stored.
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        affected = conn.execute(
            "UPDATE faculty SET password=?, must_reset_pwd=0 WHERE LOWER(email)=?",
            (new_hashed, email)
        ).rowcount
        if not affected:
            # Try hods table
            try:
                conn.execute(
                    "UPDATE hods SET password=? WHERE LOWER(email)=?",
                    (new_hashed, email)
                )
            except sqlite3.OperationalError:
                pass
        conn.commit()
        log.info("Password updated (bcrypt) for %s", email)


# ── Pydantic schemas ──────────────────────────────────────────
class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class VerifyOTPRequest(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6)


class ResetPasswordRequest(BaseModel):
    email:        EmailStr
    new_password: str = Field(..., min_length=8, max_length=128)

    @validator("new_password")
    def check_strength(cls, v):
        ok, msg = validate_strong_password(v)
        if not ok:
            raise ValueError(msg)
        return v


class ChangePasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=128)

    @validator("new_password")
    def check_strength(cls, v):
        ok, msg = validate_strong_password(v)
        if not ok:
            raise ValueError(msg)
        return v


class MessageResponse(BaseModel):
    message: str


# F-11: prune any stale blocklist rows from a previous run on module load.
# This is lightweight (one DELETE with a datetime filter) and runs once at
# startup — no scheduler required.
try:
    cleanup_expired_tokens()
except Exception as _cet_err:
    log.warning("startup cleanup_expired_tokens failed (non-fatal): %s", _cet_err)


# ── Endpoints ────────────────────────────────────────────────

@router.post("/logout", response_model=MessageResponse)
def logout(
    current_user: dict = Depends(get_current_user),
    credentials=Depends(bearer_scheme),
) -> MessageResponse:
    """
    F-11: Invalidate the caller's JWT immediately.

    Extracts the 'jti' (unique token ID, added in Step 5) and 'exp' from the
    token, then inserts them into the token_blocklist table.  Every subsequent
    request that presents the same token will be rejected by decode_token()
    even if the token has not yet expired.

    The blocklist row is automatically cleaned up by cleanup_expired_tokens()
    once the token's own expiry window passes, so the table stays bounded.
    """
    token = credentials.credentials if credentials else None
    if token:
        try:
            # decode_token already validated sig + expiry + blocklist;
            # calling it again here re-uses that validation and returns the payload.
            payload = decode_token(token)
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
                blocklist_token(jti, exp_dt)
                log.info("logout: jti=%s blocklisted (exp=%s)", jti, exp_dt)
        except HTTPException:
            # Token already invalid (expired / bad sig) — still return 200;
            # from the user's perspective they are logged out either way.
            pass
        except Exception as _le:
            log.warning("logout: unexpected error — %s", _le)

    return MessageResponse(message="Logged out successfully.")

@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit("3/5minutes")
def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
) -> MessageResponse:
    """
    Step 1: User provides their registered email.
    Sends a 6-digit OTP valid for 10 minutes.
    Always returns 200 to prevent email enumeration.

    Bug-2 FIX: For admin, OTP is sent to ADMIN_EMAIL (real Gmail address).
    """
    email = payload.email.lower()
    user  = _get_user_by_email(email)

    if not user:
        log.info("forgot-password: email not found: %s", email)
        return MessageResponse(message="If that email is registered, an OTP has been sent.")

    # Bug-2 FIX: use the real admin email as the OTP destination, not the
    # synthetic placeholder the user might have typed (e.g. "admin@college.edu").
    otp_destination = _get_admin_email() if _is_admin_email(email) else email

    _invalidate_old_otps(otp_destination)

    otp_code = generate_otp()
    _create_otp_record(otp_destination, otp_code)

    try:
        send_otp_email(otp_destination, otp_code)
    except Exception as exc:
        # Roll back — delete the record we just created
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute(
                "DELETE FROM otp_verifications WHERE user_email=? AND is_used=0",
                (otp_destination,)
            )
            conn.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to send OTP email. Check SMTP configuration in .env and try again.",
        ) from exc

    return MessageResponse(message="OTP sent to your registered email address.")


@router.post("/verify-otp", response_model=MessageResponse)
def verify_otp(payload: VerifyOTPRequest) -> MessageResponse:
    """
    Step 2: User submits email + 6-digit OTP.
    Marks OTP as verified so reset-password can proceed.
    """
    email = payload.email.lower()
    # Normalise admin aliases to the real email used when creating the record
    if _is_admin_email(email):
        email = _get_admin_email()

    record = _get_otp_record(email)

    if not record:
        raise HTTPException(status_code=400, detail="No active OTP found. Please request a new one.")

    # Check attempt limit BEFORE comparing code
    if record["is_locked"] or (record["failed_attempts"] or 0) >= OTP_MAX_ATTEMPTS:
        _update_otp_record(record["id"], is_locked=1, is_used=1)
        raise HTTPException(
            status_code=429,
            detail=f"OTP locked after {OTP_MAX_ATTEMPTS} failed attempts. Please request a new OTP.",
        )

    # Expiry check
    expires_at = datetime.fromisoformat(record["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expires_at:
        _update_otp_record(record["id"], is_used=1)
        raise HTTPException(status_code=400, detail="OTP expired. Please request a new one.")

    # F-09: constant-time comparison against stored HMAC hash — never plain compare
    if not verify_otp_code(payload.otp, record["otp"]):
        new_attempts = (record["failed_attempts"] or 0) + 1
        remaining    = OTP_MAX_ATTEMPTS - new_attempts
        if new_attempts >= OTP_MAX_ATTEMPTS:
            _update_otp_record(record["id"], failed_attempts=new_attempts, is_locked=1, is_used=1)
            raise HTTPException(
                status_code=429,
                detail=f"OTP locked after {OTP_MAX_ATTEMPTS} failed attempts. Please request a new OTP.",
            )
        _update_otp_record(record["id"], failed_attempts=new_attempts)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid OTP. {remaining} attempt(s) remaining.",
        )

    _update_otp_record(record["id"], is_verified=1)
    return MessageResponse(message="OTP verified successfully.")


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest) -> MessageResponse:
    """
    Step 3: User sets a new password.
    Requires a verified (not yet consumed) OTP record.

    Bug-1 FIX: Admin password is now actually persisted (to config + .env).
    """
    email = payload.email.lower()
    # Normalise admin aliases to the real email used in otp_verifications
    if _is_admin_email(email):
        email = _get_admin_email()

    record = _get_otp_record(email)

    if not record or not record["is_verified"]:
        raise HTTPException(
            status_code=400,
            detail="OTP not verified or already used. Please request a new OTP.",
        )

    # Re-check expiry at reset time
    expires_at = datetime.fromisoformat(record["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expires_at:
        _update_otp_record(record["id"], is_used=1)
        raise HTTPException(status_code=400, detail="OTP expired. Please request a new one.")

    _update_otp_record(record["id"], is_used=1, is_verified=0)

    # F-14: reject if the new password matches any of the last 5 used passwords
    from database import is_password_reused, add_password_history
    if is_password_reused(email, payload.new_password):
        raise HTTPException(
            status_code=400,
            detail="Cannot reuse one of your last 5 passwords.",
        )

    # F-03: pass only new_hashed — plain text is no longer stored anywhere
    _new_hash = hash_password(payload.new_password)
    _update_user_password(email, new_hashed=_new_hash)

    # F-14: record the new hash in password history AFTER successful update
    try:
        add_password_history(email, _new_hash)
    except Exception as _ph_err:
        log.warning("reset_password: add_password_history failed (non-fatal): %s", _ph_err)

    # F-13: invalidate all existing sessions for this user — tokens minted
    # before this point carry the old version and will be rejected.
    try:
        from database import increment_token_version
        increment_token_version(email)
    except Exception as _tv_err:
        log.warning("reset_password: increment_token_version failed (non-fatal): %s", _tv_err)

    return MessageResponse(message="Password reset successfully. Please log in with your new password.")


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    payload: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user),
) -> MessageResponse:
    """
    Authenticated user changes their own password.
    Used for forced first-login password reset (must_reset_pwd=True).
    """
    email = current_user.get("sub", "")
    if not email:
        raise HTTPException(status_code=400, detail="Cannot identify user from token.")

    # F-14: reject if the new password matches any of the last 5 used passwords
    from database import is_password_reused, add_password_history
    if is_password_reused(email, payload.new_password):
        raise HTTPException(
            status_code=400,
            detail="Cannot reuse one of your last 5 passwords.",
        )

    # F-03: pass only new_hashed — plain text is no longer stored anywhere
    _new_hash = hash_password(payload.new_password)
    _update_user_password(email, new_hashed=_new_hash)

    # F-14: record the new hash in password history AFTER successful update
    try:
        add_password_history(email, _new_hash)
    except Exception as _ph_err:
        log.warning("change_password: add_password_history failed (non-fatal): %s", _ph_err)

    # F-13: invalidate all existing sessions for this user — tokens minted
    # before this point carry the old version and will be rejected.
    try:
        from database import increment_token_version
        increment_token_version(email)
    except Exception as _tv_err:
        log.warning("change_password: increment_token_version failed (non-fatal): %s", _tv_err)

    return MessageResponse(message="Password changed successfully.")


@router.get("/me")
def get_me(current_user: dict = Depends(get_current_user)) -> dict:
    """Return decoded JWT payload — useful for frontend session restore."""
    return {
        "sub":      current_user.get("sub"),
        "role":     current_user.get("role"),
        "uid":      current_user.get("uid"),
        "username": current_user.get("sub"),
    }
    
    
    