"""
user_routes.py  —  Smart Attendance System v9.6 (E Auth Merge)
===============================================================
Admin user management endpoints (admin / HOD only):

  GET    /user/                    — list all staff/HOD users
  POST   /user/                    — create new user
  PUT    /user/{user_id}           — update user info
  DELETE /user/{user_id}           — delete user
  PUT    /user/{user_id}/deactivate — soft-deactivate
  PUT    /user/{user_id}/reset-password — admin resets password
  GET    /user/suggest-username    — auto-generate username

Register in api.py:
    from user_routes import router as user_router
    app.include_router(user_router, prefix="/user", tags=["User Management"])
===============================================================
"""

import re
import sqlite3
import os
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr, Field, validator

from auth_utils import admin_required, hash_password, validate_strong_password, TEMP_PASSWORD
import config

log    = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = os.path.join(config.BASE_DIR, "attendance.db")


# ── DB helpers ────────────────────────────────────────────────
def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _init_users_table():
    """Ensure a unified 'smf_users' table exists for admin-managed accounts."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS smf_users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT    NOT NULL,
                email           TEXT    UNIQUE NOT NULL,
                username        TEXT    UNIQUE NOT NULL,
                password        TEXT    NOT NULL,
                role            TEXT    NOT NULL DEFAULT 'faculty',
                department      TEXT,
                employee_id     TEXT,
                is_active       INTEGER DEFAULT 1,
                must_reset_pwd  INTEGER DEFAULT 0,
                failed_attempts INTEGER DEFAULT 0,
                is_locked       INTEGER DEFAULT 0,
                lock_until      TEXT,
                created_at      TEXT    DEFAULT (datetime('now','utc'))
            )
        """)
        conn.commit()


_init_users_table()


# ── Username helpers ──────────────────────────────────────────
def _validate_username(username: str) -> str:
    u = username.lower().strip()
    if len(u) < 3 or len(u) > 80:
        raise ValueError("Username must be 3–80 characters.")
    if not re.match(r'^[a-z0-9_]+$', u):
        raise ValueError("Username may only contain lowercase letters, numbers, and underscores.")
    if u.startswith("_") or u.endswith("_"):
        raise ValueError("Username cannot start or end with an underscore.")
    return u


def _suggest_username(
    name: str,
    role: str,
    department: Optional[str],
    employee_id: Optional[str],
) -> str:
    """
    Generate a professional username:
      admin          → admin
      hod + dept     → hod_cse
      faculty + all  → saranya_cse_101
    """
    name_slug = re.sub(r'[^a-z0-9]', '', name.lower().split()[0])
    if role == "admin":
        return "admin"
    if role == "hod":
        dept = re.sub(r'[^a-z0-9]', '', (department or "dept").lower())
        return f"hod_{dept}"
    dept = re.sub(r'[^a-z0-9]', '', (department or "").lower()) if department else ""
    eid  = re.sub(r'[^a-z0-9]', '', (employee_id or "").lower())  if employee_id else ""
    if dept and eid:
        return f"{name_slug}_{dept}_{eid}"
    if dept:
        return f"{name_slug}_{dept}"
    if eid:
        return f"{name_slug}_{eid}"
    return f"faculty_{name_slug}"


def _username_exists(username: str, exclude_id: int = None) -> bool:
    with _conn() as conn:
        if exclude_id:
            row = conn.execute(
                "SELECT 1 FROM smf_users WHERE username=? AND id!=?", (username, exclude_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM smf_users WHERE username=?", (username,)
            ).fetchone()
        return row is not None


def _email_exists(email: str, exclude_id: int = None) -> bool:
    with _conn() as conn:
        if exclude_id:
            row = conn.execute(
                "SELECT 1 FROM smf_users WHERE email=? AND id!=?", (email, exclude_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM smf_users WHERE email=?", (email,)
            ).fetchone()
        return row is not None


# ── Schemas ───────────────────────────────────────────────────
class UserCreate(BaseModel):
    name:        str            = Field(..., min_length=2, max_length=120)
    email:       EmailStr
    username:    str            = Field(..., min_length=3, max_length=80)
    role:        str            = Field(..., pattern="^(admin|hod|faculty)$")
    department:  Optional[str]  = Field(None, max_length=80)
    employee_id: Optional[str]  = Field(None, max_length=40)
    password:    Optional[str]  = None   # if omitted, temp password used

    @validator("username")
    def validate_uname(cls, v):
        return _validate_username(v)

    @validator("password")
    def validate_pwd(cls, v):
        if v is None:
            return v
        ok, msg = validate_strong_password(v)
        if not ok:
            raise ValueError(msg)
        return v


class UserOut(BaseModel):
    id:          int
    name:        str
    email:       str
    username:    str
    role:        str
    is_active:   bool
    department:  Optional[str] = None
    employee_id: Optional[str] = None
    must_reset_pwd: bool = False


class UserUpdate(BaseModel):
    name:        Optional[str]  = Field(None, min_length=2, max_length=120)
    department:  Optional[str]  = Field(None, max_length=80)
    employee_id: Optional[str]  = Field(None, max_length=40)
    is_active:   Optional[bool] = None


class AdminResetPassword(BaseModel):
    new_password: str

    @validator("new_password")
    def check_strength(cls, v):
        ok, msg = validate_strong_password(v)
        if not ok:
            raise ValueError(msg)
        return v


class MessageResponse(BaseModel):
    message: str


# ── Endpoints ────────────────────────────────────────────────

@router.get("/", response_model=list[UserOut])
def list_users(_: dict = Depends(admin_required)):
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM smf_users ORDER BY id").fetchall()
    return [dict(r) for r in rows]


@router.get("/suggest-username")
def suggest_username(
    name:        str,
    role:        str           = "faculty",
    department:  Optional[str] = None,
    employee_id: Optional[str] = None,
    _:           dict          = Depends(admin_required),
):
    """Generate a unique username suggestion for the admin UI."""
    base      = _suggest_username(name, role, department, employee_id)
    candidate = base
    counter   = 1
    while _username_exists(candidate):
        candidate = f"{base}_{counter:03d}"
        counter += 1
    return {"username": candidate}


@router.post("/", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    _:       dict = Depends(admin_required),
):
    email    = payload.email.lower()
    username = payload.username.lower()

    if _email_exists(email):
        raise HTTPException(status_code=409, detail="Email already registered.")
    if _username_exists(username):
        raise HTTPException(status_code=409, detail="Username already taken.")

    raw_pwd    = payload.password if payload.password else TEMP_PASSWORD
    must_reset = payload.password is None

    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO smf_users
               (name, email, username, password, role, department, employee_id, must_reset_pwd)
               VALUES (?,?,?,?,?,?,?,?)""",
            (payload.name, email, username,
             hash_password(raw_pwd), payload.role,
             payload.department, payload.employee_id,
             int(must_reset))
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM smf_users WHERE id=?", (cur.lastrowid,)
        ).fetchone()
    return dict(row)


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    _:       dict = Depends(admin_required),
):
    with _conn() as conn:
        row = conn.execute("SELECT * FROM smf_users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found.")
        updates, vals = [], []
        if payload.name        is not None: updates.append("name=?");        vals.append(payload.name)
        if payload.department  is not None: updates.append("department=?");  vals.append(payload.department)
        if payload.employee_id is not None: updates.append("employee_id=?"); vals.append(payload.employee_id)
        if payload.is_active   is not None: updates.append("is_active=?");   vals.append(int(payload.is_active))
        if updates:
            vals.append(user_id)
            conn.execute(f"UPDATE smf_users SET {', '.join(updates)} WHERE id=?", vals)
            conn.commit()
        row = conn.execute("SELECT * FROM smf_users WHERE id=?", (user_id,)).fetchone()
    return dict(row)


@router.put("/{user_id}/deactivate", response_model=MessageResponse)
def deactivate_user(
    user_id: int,
    _:       dict = Depends(admin_required),
):
    with _conn() as conn:
        affected = conn.execute(
            "UPDATE smf_users SET is_active=0 WHERE id=?", (user_id,)
        ).rowcount
        conn.commit()
    if not affected:
        raise HTTPException(status_code=404, detail="User not found.")
    return MessageResponse(message=f"User {user_id} deactivated.")


@router.put("/{user_id}/reset-password", response_model=MessageResponse)
def admin_reset_password(
    user_id: int,
    payload: AdminResetPassword,
    _:       dict = Depends(admin_required),
):
    with _conn() as conn:
        affected = conn.execute(
            "UPDATE smf_users SET password=?, must_reset_pwd=0 WHERE id=?",
            (hash_password(payload.new_password), user_id)
        ).rowcount
        conn.commit()
    if not affected:
        raise HTTPException(status_code=404, detail="User not found.")
    return MessageResponse(message="Password reset successfully.")


@router.delete("/{user_id}", response_model=MessageResponse)
def delete_user(
    user_id: int,
    _:       dict = Depends(admin_required),
):
    with _conn() as conn:
        affected = conn.execute(
            "DELETE FROM smf_users WHERE id=?", (user_id,)
        ).rowcount
        conn.commit()
    if not affected:
        raise HTTPException(status_code=404, detail="User not found.")
    return MessageResponse(message=f"User {user_id} deleted.")
