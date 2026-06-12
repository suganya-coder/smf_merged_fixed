


# api_hod.py  —  EduTrack Pro  HOD Management Module
#
# Provides:
#   Admin → Full authority over HOD accounts + attendance
#   HOD   → Full authority over Staff (faculty) + attendance
#   Staff → Full authority over Student attendance (existing)
#
# Routes registered on FastAPI app via register_hod_routes()
# =============================================================

import os
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

import config

log = logging.getLogger(__name__)

DB_PATH = os.path.join(config.BASE_DIR, "attendance.db")


import re

# ── Name validation helper ────────────────────────────────────
_NAME_RE_HOD = re.compile(r'^[A-Za-z]+( [A-Za-z]+)*$')

def _validate_name_hod(value: str, field: str) -> str:
    """Validate first/last name. Returns cleaned value or raises HTTPException."""
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
    if not _NAME_RE_HOD.match(v):
        raise HTTPException(status_code=422, detail=f"{field} must contain only letters and single spaces.")
    return v


# ── Mobile validation helper ──────────────────────────────────
_MOBILE_RE_HOD = re.compile(r'^[6-9][0-9]{9}$')
_EMAIL_RE_HOD  = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$')
_DISPOSABLE_DOMAINS_HOD = {
    "mailinator.com", "tempmail.com", "10minutemail.com", "guerrillamail.com",
    "throwam.com", "yopmail.com", "trashmail.com", "sharklasers.com",
    "fakeinbox.com", "maildrop.cc", "dispostable.com", "mailnull.com",
    "throwaway.email", "discard.email", "mailnesia.com", "tempinbox.com",
    "burnermail.io", "temp-mail.org", "getnada.com",
}

def _validate_email_hod(value: str, field: str = "Email", exclude_id: str = "") -> str:
    """Validate email address for HOD/faculty. Raises HTTPException on failure."""
    import database as _db
    from fastapi import HTTPException
    v = (value or "").strip()
    if not v:
        raise HTTPException(status_code=422, detail=f"{field} is required.")
    if " " in v:
        raise HTTPException(status_code=422,
                            detail=f"Spaces are not allowed in {field.lower()}.")
    if len(v) < 5:
        raise HTTPException(status_code=422,
                            detail=f"{field} is too short (minimum 5 characters).")
    if len(v) > 254:
        raise HTTPException(status_code=422,
                            detail=f"{field} is too long (maximum 254 characters).")
    if not _EMAIL_RE_HOD.match(v):
        raise HTTPException(status_code=422,
                            detail=f"Please enter a valid {field.lower()} address.")
    parts = v.split("@")
    if len(parts) != 2 or not parts[0]:
        raise HTTPException(status_code=422,
                            detail=f"Username part (before @) is missing in {field.lower()}.")
    local, domain = parts
    if not domain or domain.startswith(".") or ".." in domain:
        raise HTTPException(status_code=422,
                            detail=f"Domain part (after @) is invalid in {field.lower()}.")
    if re.search(r'[^A-Za-z0-9._%+\-]', local):
        raise HTTPException(status_code=422,
                            detail=f"{field} contains invalid characters.")
    if domain.lower() in _DISPOSABLE_DOMAINS_HOD:
        raise HTTPException(status_code=422,
                            detail="Temporary/disposable email addresses are not allowed.")
    dup = _db.check_email_duplicate(v, exclude_id)
    if dup.get("exists"):
        role = dup.get("role", "record")
        name = dup.get("name", "")
        raise HTTPException(status_code=409,
                            detail=f"This email address is already registered "
                                   f"to {name} ({role}).")
    return v



def _validate_mobile_hod(value: str, field: str = "Mobile Number",
                          exclude_id: str = "") -> str:
    """Validate Indian mobile number. Raises HTTPException on failure."""
    import database as _db
    from fastapi import HTTPException
    v = (value or "").strip()
    if not v:
        raise HTTPException(status_code=422, detail=f"{field} is required.")
    if re.search(r'[A-Za-z]', v):
        raise HTTPException(status_code=422,
                            detail=f"{field} must not contain letters.")
    if re.search(r'[^0-9]', v):
        raise HTTPException(status_code=422,
                            detail=f"{field} must not contain special characters.")
    if len(v) != 10:
        raise HTTPException(status_code=422,
                            detail=f"{field} must be exactly 10 digits.")
    if not _MOBILE_RE_HOD.match(v):
        raise HTTPException(status_code=422,
                            detail=f"{field} must start with 6, 7, 8, or 9.")
    dup = _db.check_mobile_duplicate(v, exclude_id)
    if dup.get("exists"):
        role = dup.get("role", "record")
        name = dup.get("name", "")
        raise HTTPException(status_code=409,
                            detail=f"{field} {v} is already registered "
                                   f"to {name} ({role}).")
    return v


# =============================================================
# DB CONTEXT
# =============================================================
@contextmanager
def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


# =============================================================
# TABLE SETUP
# =============================================================
def _ensure_hod_tables():
    """
    Create HOD tables if they don't exist, and safely migrate older DBs.
    Each statement is run individually so a pre-existing table with
    missing columns does NOT abort the whole migration.
    """
    with _conn() as c:
        # ── hods table ────────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS hods (
                hod_id      TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                dept        TEXT NOT NULL DEFAULT '',
                designation TEXT NOT NULL DEFAULT 'Head of Department',
                email       TEXT,
                mobile      TEXT,
                password    TEXT DEFAULT 'Hod@123',
                joined_on   TEXT,
                active      INTEGER DEFAULT 1
            )
        """)

        # ── hod_attendance table ──────────────────────────────────────
        # Check which columns already exist (handles old schemas)
        att_cols = [row[1] for row in
                    c.execute("PRAGMA table_info(hod_attendance)").fetchall()]

        if not att_cols:
            # Table does not exist at all — create fresh with full schema
            c.execute("""
                CREATE TABLE hod_attendance (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    hod_id       TEXT NOT NULL,
                    att_date     TEXT NOT NULL DEFAULT '',
                    status       TEXT NOT NULL DEFAULT 'present',
                    arrival_time TEXT,
                    reason       TEXT,
                    updated_by   TEXT,
                    created_at   TEXT DEFAULT (datetime('now','localtime'))
                )
            """)
        else:
            # Table exists — add any missing columns individually
            needed = [
                ("att_date",     "TEXT NOT NULL DEFAULT ''"),
                ("arrival_time", "TEXT"),
                ("reason",       "TEXT"),
                ("updated_by",   "TEXT"),
                ("created_at",   "TEXT DEFAULT (datetime('now','localtime'))"),
            ]
            for col, defn in needed:
                if col not in att_cols:
                    try:
                        c.execute(
                            f"ALTER TABLE hod_attendance ADD COLUMN {col} {defn}")
                        log.info("hod_attendance: added missing column '%s'", col)
                    except Exception as _ae:
                        log.warning("Could not add column %s: %s", col, _ae)

            # FIX v10.5: role_attendance_session.py writes column 'date',
            # but api_hod.py reads column 'att_date'.
            # Ensure 'date' column exists (written by role_attendance_session)
            # and keep att_date in sync on every startup.
            if "date" not in att_cols:
                try:
                    c.execute("ALTER TABLE hod_attendance ADD COLUMN date TEXT DEFAULT ''")
                    log.info("hod_attendance: added missing column 'date'")
                except Exception as _ae:
                    log.warning("Could not add column date: %s", _ae)

            # Copy 'date' → 'att_date' for rows written by role_attendance_session
            try:
                c.execute(
                    "UPDATE hod_attendance SET att_date = date "
                    "WHERE (att_date IS NULL OR att_date = '') AND date IS NOT NULL AND date != ''"
                )
            except Exception:
                pass

            # Copy 'att_date' → 'date' for rows written via API/manual entry
            try:
                c.execute(
                    "UPDATE hod_attendance SET date = att_date "
                    "WHERE (date IS NULL OR date = '') AND att_date IS NOT NULL AND att_date != ''"
                )
            except Exception:
                pass

        # ── Indexes (safe — IF NOT EXISTS) ───────────────────────────
        for sql in [
            "CREATE INDEX IF NOT EXISTS idx_hod_att_date  ON hod_attendance(att_date)",
            "CREATE INDEX IF NOT EXISTS idx_hod_att_hodid ON hod_attendance(hod_id)",
        ]:
            try:
                c.execute(sql)
            except Exception:
                pass

        # ── Migrate hods table columns ────────────────────────────────
        hod_cols = [row[1] for row in
                    c.execute("PRAGMA table_info(hods)").fetchall()]
        for col, defn in [
            ("designation", "TEXT DEFAULT 'Head of Department'"),
            ("password",    "TEXT DEFAULT 'Hod@123'"),
        ]:
            if col not in hod_cols:
                try:
                    c.execute(f"ALTER TABLE hods ADD COLUMN {col} {defn}")
                except Exception:
                    pass

        # ── FIX v11.1: repair HODs stuck at active=0 ─────────────────────
        # Any HOD that exists in the table should be visible unless it was
        # explicitly soft-deleted via the admin UI.  Pre-existing / migrated
        # databases shipped with active=0 cause "No HODs found" in the
        # frontend.  Reset them unconditionally; the only path to active=0
        # after this point is a deliberate DELETE action by the admin.
        c.execute("UPDATE hods SET active=1 WHERE active IS NULL OR active=0")

        # ── FIX v11.1: normalise dept codes to match frontend dropdown ────
        # migrate_hod_to_hods.py and older enrollment paths stored 'CS',
        # 'cse', 'Ece' etc.  The frontend filter queries dept='CSE', so rows
        # with non-canonical codes never match.  Normalise once at startup.
        _DEPT_NORM = [
            ("CS",               "CSE"), ("cse", "CSE"), ("cs", "CSE"),
            ("Computer Science", "CSE"), ("computer science", "CSE"),
            ("Ece",  "ECE"), ("ece", "ECE"), ("Electronics", "ECE"),
            ("Mech", "MECH"), ("mech", "MECH"), ("Mechanical", "MECH"),
            ("It",   "IT"),  ("it",   "IT"),   ("Information Technology", "IT"),
            ("Eee",  "EEE"), ("eee",  "EEE"),  ("Electrical", "EEE"),
            ("Civil", "CIVIL"), ("civil", "CIVIL"),
        ]
        for wrong, right in _DEPT_NORM:
            try:
                c.execute("UPDATE hods SET dept=? WHERE dept=?", (right, wrong))
            except Exception:
                pass

        _seed_demo_hods(c)


def _seed_demo_hods(conn):
    """Seed demo HODs on first run; also re-activates any that were deactivated.

    FIX v11.1: Changed from plain INSERT (skipped when count>0) to UPSERT.
    This means demo HODs that exist but have active=0 (e.g. from a legacy DB)
    get their active flag restored without overwriting real admin changes.
    Also corrected dept values from 'CS'/'ECE'/'MECH' → canonical 'CSE'/'ECE'/'MECH'
    so they match the frontend department dropdown exactly.
    """
    demo = [
        ("HOD001", "Dr. S. Rajendran",   "CSE",  "Head of Department",
         "rajendran@college.edu",  "9800011111", "Hod@123"),
        ("HOD002", "Dr. P. Meenakshi",   "ECE",  "Head of Department",
         "meenakshi@college.edu",  "9800022222", "Hod@123"),
        ("HOD003", "Prof. K. Vijayaraj", "MECH", "Head of Department",
         "vijayaraj@college.edu",  "9800033333", "Hod@123"),
    ]
    today = datetime.now().strftime("%Y-%m-%d")
    for row in demo:
        try:
            conn.execute(
                "INSERT INTO hods"
                "(hod_id,name,dept,designation,email,mobile,password,joined_on,active)"
                " VALUES (?,?,?,?,?,?,?,?,1)"
                " ON CONFLICT(hod_id) DO UPDATE SET"
                "   active=1,"
                "   dept=CASE WHEN hods.dept IN ('','CS','cs','cse')"
                "             THEN excluded.dept ELSE hods.dept END",
                (*row, today)
            )
        except Exception:
            pass


# =============================================================
# DATA HELPERS
# =============================================================
def get_all_hods(dept: str = None, search: str = None) -> list:
    with _conn() as c:
        q  = "SELECT * FROM hods WHERE active=1"
        params = []
        if dept:
            q += " AND dept=?"
            params.append(dept)
        if search:
            q += " AND (name LIKE ? OR hod_id LIKE ? OR email LIKE ?)"
            params += [f"%{search}%", f"%{search}%", f"%{search}%"]
        q += " ORDER BY dept, name"
        rows = c.execute(q, params).fetchall()
        return [_enrich_hod(dict(r), c) for r in rows]


def _enrich_hod(hod: dict, conn) -> dict:
    """Attach live attendance stats to a HOD record."""
    hid = hod["hod_id"]
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    today  = datetime.now().strftime("%Y-%m-%d")
    # FIX v10.5: COALESCE(att_date, date) so rows written by role_attendance_session
    # (which uses column 'date') are counted alongside rows written by the manual API
    # (which uses column 'att_date').
    row = conn.execute("""
        SELECT
            COUNT(*) AS total_logged,
            SUM(CASE WHEN LOWER(status) IN ('present','Present') THEN 1 ELSE 0 END) AS present_days,
            SUM(CASE WHEN COALESCE(NULLIF(att_date,''), date, '')=? THEN 1 ELSE 0 END) AS marked_today
        FROM hod_attendance
        WHERE hod_id=? AND COALESCE(NULLIF(att_date,''), date, '')>=?
    """, (today, hid, cutoff)).fetchone()
    hod["present_days"] = row["present_days"] or 0
    hod["total_logged"]  = row["total_logged"]  or 0
    hod["marked_today"]  = row["marked_today"]  or 0
    hod["att_pct"] = (
        round(hod["present_days"] / hod["total_logged"] * 100)
        if hod["total_logged"] > 0 else 0
    )
    # Don't expose password over API
    hod.pop("password", None)
    return hod


def get_hod(hod_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM hods WHERE hod_id=? AND active=1", (hod_id,)
        ).fetchone()
        if not row:
            return None
        h = dict(row)
        h.pop("password", None)
        return _enrich_hod(h, c)


def create_hod(hod_id: str, name: str, dept: str,
               designation: str = "Head of Department",
               email: str = "", mobile: str = "",
               password: str = "Hod@123") -> dict:
    """Create or reactivate a HOD record.

    FIX v11.1: Changed from plain INSERT to UPSERT.
    - If the HOD is new → inserted with active=1.
    - If the HOD already exists (e.g. was deactivated or seeded with active=0)
      → reactivated and fields updated, password preserved if already set.
    - dept is normalised to uppercase canonical form.
    """
    # Normalise dept
    _DEPT_ALIASES = {
        "cs": "CSE", "cse": "CSE", "computer science": "CSE",
        "ece": "ECE", "electronics": "ECE",
        "it": "IT",  "information technology": "IT",
        "mech": "MECH", "mechanical": "MECH",
        "eee": "EEE", "electrical": "EEE",
        "civil": "CIVIL",
    }
    dept_norm = _DEPT_ALIASES.get(dept.strip().lower(), dept.strip().upper())

    today = datetime.now().strftime("%Y-%m-%d")
    with _conn() as c:
        c.execute("""
            INSERT INTO hods(hod_id,name,dept,designation,email,mobile,password,joined_on,active)
            VALUES (?,?,?,?,?,?,?,?,1)
            ON CONFLICT(hod_id) DO UPDATE SET
                name        = excluded.name,
                dept        = excluded.dept,
                designation = excluded.designation,
                email       = CASE WHEN excluded.email != '' THEN excluded.email ELSE hods.email END,
                mobile      = CASE WHEN excluded.mobile != '' THEN excluded.mobile ELSE hods.mobile END,
                password    = CASE WHEN excluded.password NOT IN ('','Hod@123')
                                   THEN excluded.password ELSE hods.password END,
                active      = 1
        """, (hod_id, name, dept_norm, designation, email, mobile, password, today))
    return {"hod_id": hod_id, "status": "created"}


def update_hod(hod_id: str, name: str, dept: str,
               designation: str, email: str, mobile: str,
               password: str | None = None) -> dict:
    with _conn() as c:
        if password:
            c.execute("""
                UPDATE hods SET name=?,dept=?,designation=?,email=?,mobile=?,password=?
                WHERE hod_id=?
            """, (name, dept, designation, email, mobile, password, hod_id))
        else:
            c.execute("""
                UPDATE hods SET name=?,dept=?,designation=?,email=?,mobile=?
                WHERE hod_id=?
            """, (name, dept, designation, email, mobile, hod_id))
    return {"hod_id": hod_id, "status": "updated"}


def deactivate_hod(hod_id: str) -> dict:
    """Delete HOD from hods table and clean up related attendance records."""
    with _conn() as c:
        # Hard delete from hods table
        c.execute("DELETE FROM hods WHERE hod_id=?", (hod_id,))
        # Clean up attendance records
        try:
            c.execute("DELETE FROM hod_attendance WHERE hod_id=?", (hod_id,))
        except Exception:
            pass
    return {"hod_id": hod_id, "status": "deleted"}


def get_hod_attendance(hod_id: str, days: int = 30) -> list:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _conn() as c:
        rows = c.execute("""
            SELECT * FROM hod_attendance
            WHERE hod_id=? AND att_date>=?
            ORDER BY att_date DESC
        """, (hod_id, cutoff)).fetchall()
    return [dict(r) for r in rows]


def mark_hod_attendance(hod_id: str, att_date: str, status: str,
                        arrival_time: str = None, reason: str = "",
                        updated_by: str = "ADMIN") -> dict:
    with _conn() as c:
        c.execute("""
            INSERT INTO hod_attendance(hod_id,att_date,status,arrival_time,reason,updated_by)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(hod_id,att_date) DO UPDATE SET
                status=excluded.status,
                arrival_time=excluded.arrival_time,
                reason=excluded.reason,
                updated_by=excluded.updated_by,
                created_at=datetime('now','localtime')
        """, (hod_id, att_date, status, arrival_time, reason, updated_by))
    return {"hod_id": hod_id, "att_date": att_date, "status": status}


def edit_hod_attendance(log_id: int, status: str,
                        arrival_time: str = None, reason: str = "",
                        updated_by: str = "ADMIN") -> dict:
    with _conn() as c:
        c.execute("""
            UPDATE hod_attendance
            SET status=?, arrival_time=?, reason=?, updated_by=?
            WHERE id=?
        """, (status, arrival_time, reason, updated_by, log_id))
    return {"log_id": log_id, "status": "updated"}


def delete_hod_attendance(log_id: int) -> dict:
    with _conn() as c:
        c.execute("DELETE FROM hod_attendance WHERE id=?", (log_id,))
    return {"log_id": log_id, "status": "deleted"}


def get_hod_by_credentials(identifier: str, password: str) -> dict | None:
    """Return HOD dict if identifier (hod_id or email) + password match.

    Supports both:
      - Plain-text passwords (stored during enrollment via add_hod default)
      - bcrypt-hashed passwords (stored after a forgot-password / change-password reset)

    BUG FIX: Previously used plain == plain comparison only.
    After a password reset, auth_routes._update_user_password() stores a bcrypt
    hash, so the old plain comparison always failed → "Invalid credentials".
    Now we detect the hash prefix ($2b$ / $2a$) and use verify_password() for
    hashed values, falling back to plain-text equality for legacy rows.
    """
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM hods WHERE (hod_id=? OR email=?) AND active=1",
            (identifier, identifier)
        ).fetchone()
        if not row:
            return None
        h = dict(row)
        stored = h.get("password", "") or ""
        # Detect bcrypt hash (starts with $2b$ or $2a$)
        if stored.startswith(("$2b$", "$2a$")):
            try:
                from auth_utils import verify_password
                matched = verify_password(password, stored)
            except Exception:
                matched = False
        else:
            # Legacy plain-text comparison (default Hod@123 rows)
            matched = (stored == password)
        if matched:
            h.pop("password", None)
            return h
    return None


def get_hod_analytics() -> dict:
    """Summary stats for admin dashboard."""
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM hods WHERE active=1").fetchone()[0]
        depts = c.execute(
            "SELECT dept, COUNT(*) as cnt FROM hods WHERE active=1 GROUP BY dept"
        ).fetchall()
        today = datetime.now().strftime("%Y-%m-%d")
        # FIX v10.5: use COALESCE(att_date, date) — face-recognition session
        # writes column 'date'; manual/API entry writes 'att_date'.
        present_today = c.execute(
            "SELECT COUNT(*) FROM hod_attendance "
            "WHERE COALESCE(NULLIF(att_date,''), date, '')=? "
            "AND LOWER(status) IN ('present','Present')",
            (today,)
        ).fetchone()[0]
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        recent = c.execute("""
            SELECT h.hod_id, h.name, h.dept,
                   COUNT(a.id) AS logged,
                   SUM(CASE WHEN LOWER(a.status) IN ('present','Present') THEN 1 ELSE 0 END) AS present
            FROM hods h
            LEFT JOIN hod_attendance a ON h.hod_id=a.hod_id
                AND COALESCE(NULLIF(a.att_date,''), a.date, '')>=?
            WHERE h.active=1
            GROUP BY h.hod_id
            ORDER BY present DESC
        """, (cutoff,)).fetchall()
    return {
        "total_hods":     total,
        "present_today":  present_today,
        "absent_today":   total - present_today,
        "dept_breakdown": [dict(r) for r in depts],
        "hod_summary":    [dict(r) for r in recent],
    }


# =============================================================
# ROUTE REGISTRATION
# =============================================================

def _validate_dob_hod(dob_str: str) -> str:
    """
    Validate date_of_birth for HOD enrollment.
    Raises HTTPException(422) on any violation.
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

    # Step 6 - HOD must be at least 35
    if age < 35:
        raise HTTPException(status_code=422,
                            detail="HOD must be at least 35 years old.")

    return parsed.strftime("%Y-%m-%d")


def register_hod_routes(app, get_current_user, admin_required, _uname):
    """
    Mount all HOD management API routes onto the FastAPI app.
    Call this inside create_app() in api.py.
    """
    from fastapi import Depends, HTTPException
    from pydantic import BaseModel
    from typing import Optional

    # ── Pydantic models ──────────────────────────────────────
    class CreateHodReq(BaseModel):
        hod_id:        str
        # Frontend sends first_name+last_name; name is optional alias
        name:          Optional[str] = ""
        first_name:    Optional[str] = ""
        last_name:     Optional[str] = ""
        # Frontend sends department; backend uses dept — accept both
        dept:          Optional[str] = ""
        department:    Optional[str] = ""
        designation:   Optional[str] = "Head of Department"
        email:         Optional[str] = ""
        mobile:        Optional[str] = ""
        password:      Optional[str] = "Hod@123"
        gender:        Optional[str] = ""
        date_of_birth: Optional[str] = ""
        joining_date:  Optional[str] = ""
        employee_code: Optional[str] = ""
        role:          Optional[str] = "hod"

    class UpdateHodReq(BaseModel):
        name:        str
        dept:        str
        designation: Optional[str] = "Head of Department"
        email:       Optional[str] = ""
        mobile:      Optional[str] = ""
        password:    Optional[str] = None   # None = don't change password

    class HodAttReq(BaseModel):
        hod_id:      str
        att_date:    str
        status:      str
        arrival_time: Optional[str] = None
        reason:      Optional[str] = ""
        updated_by:  str = "ADMIN"

    class HodAttEditReq(BaseModel):
        status:       str
        arrival_time: Optional[str] = None
        reason:       Optional[str] = ""
        updated_by:   str = "ADMIN"

    # ── Ensure tables at startup ─────────────────────────────
    try:
        _ensure_hod_tables()
    except Exception as e:
        log.error("HOD table init failed: %s", e)

    # ===========================================================
    # HOD CRUD  (Admin only)
    # ===========================================================

    @app.get("/api/hods/analytics")
    def api_hod_analytics(_: dict = Depends(admin_required)):
        """Summary KPIs for the HOD management dashboard."""
        try:
            return get_hod_analytics()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/hods")
    def api_list_hods(dept: str = None, search: str = None,
                      _: dict = Depends(get_current_user)):
        """List all active HODs (any authenticated user can view)."""
        try:
            return get_all_hods(dept=dept, search=search)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/hods")
    def api_create_hod(req: CreateHodReq,
                       user: dict = Depends(admin_required)):
        """Create or reactivate a HOD account (admin only).

        FIX v11.1: Removed the hard 409 duplicate-check so that re-enrolling
        an existing (possibly deactivated) HOD via the frontend works correctly.
        create_hod() now uses an UPSERT that sets active=1 and updates fields.
        """
        # Resolve name: frontend may send first_name+last_name or combined name
        # Strict name validation (backend) — only when first_name/last_name are supplied
        if req.first_name or req.last_name:
            req.first_name = _validate_name_hod(req.first_name or "", "First Name")
            req.last_name  = _validate_name_hod(req.last_name or "",  "Last Name")
        # Strict mobile validation (backend)
        if req.mobile:
            req.mobile = _validate_mobile_hod(
                req.mobile, "Mobile Number",
                (req.hod_id or "").strip().upper()
            )
        # Strict email validation (backend)
        if req.email:
            req.email = _validate_email_hod(
                req.email, "Email",
                (req.hod_id or "").strip().upper()
            )
        # Strict DOB validation (backend, Step 7-8 of validation flow)
        req.date_of_birth = _validate_dob_hod(req.date_of_birth or "")
        resolved_name = (req.name or "").strip()
        if not resolved_name:
            resolved_name = ((req.first_name or "") + " " + (req.last_name or "")).strip()
        if not resolved_name:
            resolved_name = req.hod_id  # fallback

        # Resolve dept: frontend sends 'department', backend stores 'dept'
        resolved_dept = (req.dept or req.department or "").strip().upper()

        try:
            result = create_hod(
                hod_id      = req.hod_id.strip().upper(),
                name        = resolved_name,
                dept        = resolved_dept,
                designation = req.designation or "Head of Department",
                email       = (req.email or "").strip(),
                mobile      = (req.mobile or "").strip(),
                password    = req.password or "Hod@123",
            )
            # Also write extra fields into the hods row if the columns exist
            try:
                with _conn() as c:
                    c.execute("""
                        UPDATE hods SET
                            first_name=?, last_name=?, gender=?,
                            date_of_birth=?, joining_date=?,
                            employee_code=?, role=?, enrolled_on=?
                        WHERE hod_id=?
                    """, (
                        (req.first_name or "").strip(),
                        (req.last_name  or "").strip(),
                        (req.gender     or "").strip(),
                        (req.date_of_birth or "").strip(),
                        (req.joining_date  or "").strip(),
                        (req.employee_code or "").strip(),
                        (req.role or "hod"),
                        __import__('datetime').datetime.now().strftime("%Y-%m-%d"),
                        req.hod_id.strip().upper()
                    ))
            except Exception:
                pass  # extra fields are optional; don't fail enrollment for this
            log.info("HOD created: %s by %s", req.hod_id, _uname(user))
            return result
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/hods/{hod_id}")
    def api_get_hod(hod_id: str,
                    _: dict = Depends(get_current_user)):
        """Get a single HOD by ID."""
        h = get_hod(hod_id.upper())
        if not h:
            raise HTTPException(status_code=404, detail="HOD not found")
        return h

    @app.put("/api/hods/{hod_id}")
    def api_update_hod(hod_id: str, req: UpdateHodReq,
                       user: dict = Depends(admin_required)):
        """Update HOD details (admin only)."""
        h = get_hod(hod_id.upper())
        if not h:
            raise HTTPException(status_code=404, detail="HOD not found")
        try:
            result = update_hod(
                hod_id      = hod_id.upper(),
                name        = req.name.strip(),
                dept        = req.dept.strip().upper(),
                designation = req.designation or "Head of Department",
                email       = req.email.strip() if req.email else "",
                mobile      = req.mobile.strip() if req.mobile else "",
                password    = req.password if req.password else None,
            )
            log.info("HOD updated: %s by %s", hod_id, _uname(user))
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/hods/{hod_id}")
    def api_delete_hod(hod_id: str,
                       user: dict = Depends(admin_required)):
        """Deactivate a HOD account (admin only)."""
        h = get_hod(hod_id.upper())
        if not h:
            raise HTTPException(status_code=404, detail="HOD not found")
        result = deactivate_hod(hod_id.upper())
        log.info("HOD deactivated: %s by %s", hod_id, _uname(user))
        return result

    # ===========================================================
    # HOD ATTENDANCE  (Admin can mark/edit/delete)
    # ===========================================================

    @app.get("/api/hods/{hod_id}/attendance")
    def api_get_hod_attendance(hod_id: str, days: int = 30,
                               _: dict = Depends(get_current_user)):
        """Get attendance log for a HOD."""
        try:
            return get_hod_attendance(hod_id.upper(), days=days)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/hods/attendance")
    def api_mark_hod_att(req: HodAttReq,
                         user: dict = Depends(admin_required)):
        """Mark or update HOD attendance for a specific date (admin only)."""
        try:
            result = mark_hod_attendance(
                hod_id       = req.hod_id.upper(),
                att_date     = req.att_date,
                status       = req.status,
                arrival_time = req.arrival_time,
                reason       = req.reason or "",
                updated_by   = _uname(user),
            )
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/api/hods/{hod_id}/attendance/{log_id}")
    def api_edit_hod_att(hod_id: str, log_id: int,
                         req: HodAttEditReq,
                         user: dict = Depends(admin_required)):
        """Edit a specific HOD attendance record (admin only)."""
        try:
            result = edit_hod_attendance(
                log_id       = log_id,
                status       = req.status,
                arrival_time = req.arrival_time,
                reason       = req.reason or "",
                updated_by   = _uname(user),
            )
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/hods/{hod_id}/attendance/{log_id}")
    def api_delete_hod_att(hod_id: str, log_id: int,
                           user: dict = Depends(admin_required)):
        """Delete a HOD attendance record (admin only)."""
        try:
            return delete_hod_attendance(log_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


    # ===========================================================
    # BACKWARD-COMPAT ALIASES  /api/hod  ->  /api/hods
    # (Frontend legacy calls — do not remove)
    # ===========================================================

    @app.get("/api/hod")
    def api_hod_list_alias(dept: str = None, search: str = None,
                           _: dict = Depends(get_current_user)):
        """Alias: GET /api/hod -> GET /api/hods (backward compat)."""
        try:
            return get_all_hods(dept=dept, search=search)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/hod")
    def api_hod_create_alias(req: CreateHodReq,
                             user: dict = Depends(admin_required)):
        """Alias: POST /api/hod -> POST /api/hods (backward compat).

        FIX v11.1: Removed 409 duplicate-check; create_hod now upserts.
        """
        try:
            result = create_hod(
                hod_id      = req.hod_id.strip().upper(),
                name        = (req.name or req.hod_id).strip(),
                dept        = (req.dept or req.department or "").strip().upper(),
                designation = req.designation or "Head of Department",
                email       = (req.email or "").strip(),
                mobile      = (req.mobile or "").strip(),
                password    = req.password or "Hod@123",
            )
            log.info("HOD created via alias /api/hod: %s by %s", req.hod_id, _uname(user))
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/hod/enroll")
    def api_hod_enroll_alias(req: CreateHodReq,
                             user: dict = Depends(admin_required)):
        """Alias: POST /api/hod/enroll -> POST /api/hods (backward compat).

        FIX v11.1: Removed 409 duplicate-check; create_hod now upserts.
        """
        try:
            result = create_hod(
                hod_id      = req.hod_id.strip().upper(),
                name        = (req.name or req.hod_id).strip(),
                dept        = (req.dept or req.department or "").strip().upper(),
                designation = req.designation or "Head of Department",
                email       = (req.email or "").strip(),
                mobile      = (req.mobile or "").strip(),
                password    = req.password or "Hod@123",
            )
            log.info("HOD enrolled via /api/hod/enroll: %s by %s", req.hod_id, _uname(user))
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    log.info("HOD management routes registered.")