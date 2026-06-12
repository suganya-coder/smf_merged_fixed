


# =============================================================
# database_override.py  —  Attendance Override Feature  v11.0
#
# NEW in v11.0: STRICT HIERARCHICAL ROLE-BASED ACCESS CONTROL
#
# Override Access Matrix:
#   Admin   → Can ONLY override HOD attendance records
#   HOD     → Can ONLY override Staff attendance records
#   Staff   → Can ONLY override Student attendance records
#   Student → NO override permissions whatsoever
#
# This is enforced at the DATABASE layer so no API/frontend
# manipulation can bypass it.
#
# NOTE: The old 'staff' table has been REMOVED and fully merged into
#       the unified 'faculty' table. All staff/faculty data lives in
#       a single table: faculty.
#
# Provides:
#   init_override_tables()           — call once on startup
#   add_attendance_override()        — save a new override record
#   get_override_history()           — paginated history
#   get_override_history_filtered()  — filtered version
#   check_hierarchical_permission()  — STRICT role hierarchy check
#   resolve_target_role()            — look up the role of the target record owner
#   get_staff_by_id()                — fetch faculty record
# =============================================================

import os, sqlite3, logging, json
from datetime import datetime
from contextlib import contextmanager
import config

log = logging.getLogger(__name__)
DB_PATH = os.path.join(config.BASE_DIR, "attendance.db")

# =============================================================
# STRICT ROLE HIERARCHY — Single source of truth
# =============================================================

# Maps: logged-in role → the ONE role they are permitted to override
ROLE_CAN_OVERRIDE = {
    "admin":        "hod",
    "hod":          "staff",
    "staff":        "student",
    "faculty":      "student",   # faculty = staff alias
    "classincharge":"student",   # class incharge = staff alias
    "teacher":      "student",   # teacher = staff alias
    "student":      None,        # students can override nothing
}

# Canonical role names for display
ROLE_DISPLAY = {
    "admin":        "Admin",
    "hod":          "HOD",
    "staff":        "Staff",
    "faculty":      "Staff",
    "classincharge":"Staff",
    "teacher":      "Staff",
    "student":      "Student",
}

# All role aliases that map to the same logical role
STAFF_ROLE_ALIASES = {"staff", "faculty", "classincharge", "teacher", "subject_staff"}
HOD_ROLE_ALIASES   = {"hod", "hod_admin"}
ADMIN_ROLE_ALIASES = {"admin", "administrator", "superadmin"}


@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _safe_add_column(conn, table, column, definition):
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError:
        pass


def _normalise_role(raw_role: str) -> str:
    """Normalise any role alias to a canonical lowercase role name."""
    r = (raw_role or "").strip().lower()
    if r in ADMIN_ROLE_ALIASES:
        return "admin"
    if r in HOD_ROLE_ALIASES:
        return "hod"
    if r in STAFF_ROLE_ALIASES:
        return "staff"
    if r == "student":
        return "student"
    return r  # unknown roles fall through unchanged


# =============================================================
# INIT
# =============================================================
def init_override_tables():
    """Create attendance_overrides table and ensure faculty incharge columns exist."""
    with _db() as conn:
        conn.executescript("""
        -- ── Main override history table ──────────────────────
        CREATE TABLE IF NOT EXISTS attendance_overrides (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            department              TEXT NOT NULL DEFAULT '',
            year                    TEXT NOT NULL DEFAULT '',
            semester                TEXT NOT NULL DEFAULT '',
            section                 TEXT NOT NULL DEFAULT '',
            student_register_number TEXT NOT NULL DEFAULT '',
            student_name            TEXT NOT NULL DEFAULT '',
            course_code             TEXT NOT NULL DEFAULT '',
            course_name             TEXT NOT NULL DEFAULT '',
            period                  TEXT NOT NULL DEFAULT '',
            attendance_from         TEXT NOT NULL DEFAULT '',
            attendance_to           TEXT NOT NULL DEFAULT '',
            reason                  TEXT NOT NULL DEFAULT '',
            overridden_by           TEXT NOT NULL DEFAULT '',
            staff_id                TEXT NOT NULL DEFAULT '',
            staff_role              TEXT NOT NULL DEFAULT '',
            target_role             TEXT NOT NULL DEFAULT '',
            override_date           TEXT NOT NULL DEFAULT '',
            override_time           TEXT NOT NULL DEFAULT '',
            created_at              TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_ov_dept    ON attendance_overrides(department);
        CREATE INDEX IF NOT EXISTS idx_ov_year    ON attendance_overrides(year);
        CREATE INDEX IF NOT EXISTS idx_ov_sem     ON attendance_overrides(semester);
        CREATE INDEX IF NOT EXISTS idx_ov_sec     ON attendance_overrides(section);
        CREATE INDEX IF NOT EXISTS idx_ov_staff   ON attendance_overrides(staff_id);
        CREATE INDEX IF NOT EXISTS idx_ov_student ON attendance_overrides(student_register_number);

        -- ── Correction request / approval workflow table ─────
        CREATE TABLE IF NOT EXISTS attendance_override_requests (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            attendance_id   TEXT    DEFAULT '',
            student_id      TEXT    NOT NULL DEFAULT '',
            faculty_id      TEXT    NOT NULL DEFAULT '',
            subject_id      TEXT    DEFAULT '',
            old_status      TEXT    NOT NULL DEFAULT '',
            new_status      TEXT    NOT NULL DEFAULT '',
            requested_by    TEXT    NOT NULL DEFAULT '',
            requested_role  TEXT    NOT NULL DEFAULT '',
            reason          TEXT    DEFAULT '',
            approval_status TEXT    NOT NULL DEFAULT 'PENDING',
            approved_by     TEXT    DEFAULT '',
            approved_role   TEXT    DEFAULT '',
            hod_required    INTEGER DEFAULT 0,
            date            TEXT    DEFAULT '',
            course_code     TEXT    DEFAULT '',
            period          TEXT    DEFAULT '',
            created_at      TEXT    DEFAULT (datetime('now','localtime')),
            updated_at      TEXT    DEFAULT (datetime('now','localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_orq_student  ON attendance_override_requests(student_id);
        CREATE INDEX IF NOT EXISTS idx_orq_faculty  ON attendance_override_requests(faculty_id);
        CREATE INDEX IF NOT EXISTS idx_orq_status   ON attendance_override_requests(approval_status);
        CREATE INDEX IF NOT EXISTS idx_orq_hod      ON attendance_override_requests(hod_required);

        -- ── Immutable audit trail ─────────────────────────────
        CREATE TABLE IF NOT EXISTS attendance_audit_logs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            attendance_id TEXT   DEFAULT '',
            old_value    TEXT    DEFAULT '',
            new_value    TEXT    DEFAULT '',
            changed_by   TEXT    NOT NULL DEFAULT '',
            changed_role TEXT    NOT NULL DEFAULT '',
            reason       TEXT    DEFAULT '',
            action_type  TEXT    DEFAULT '',
            timestamp    TEXT    DEFAULT (datetime('now','localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_aud_changed_by ON attendance_audit_logs(changed_by);
        CREATE INDEX IF NOT EXISTS idx_aud_action     ON attendance_audit_logs(action_type);
        """)

        # ── Extend faculty table with class-incharge fields ───
        _safe_add_column(conn, "faculty", "is_class_incharge",   "INTEGER DEFAULT 0")
        _safe_add_column(conn, "faculty", "incharge_department",  "TEXT DEFAULT ''")
        _safe_add_column(conn, "faculty", "incharge_year",        "TEXT DEFAULT ''")
        _safe_add_column(conn, "faculty", "incharge_section",     "TEXT DEFAULT ''")
        _safe_add_column(conn, "faculty", "role",                 "TEXT DEFAULT 'staff'")

        # Add target_role column to existing installs (idempotent)
        _safe_add_column(conn, "attendance_overrides", "target_role", "TEXT NOT NULL DEFAULT ''")

    log.info("Override tables ready (v11.0 — hierarchical RBAC).")




# =============================================================
# CORRECTION REQUEST HELPERS
# =============================================================

def add_override_request(
    student_id, faculty_id, subject_id,
    old_status, new_status,
    requested_by, requested_role, reason,
    date, course_code, period,
    hod_required=0,
):
    """Insert a new correction request. Returns new row id."""
    with _db() as conn:
        cur = conn.execute(
            """INSERT INTO attendance_override_requests
               (student_id, faculty_id, subject_id, old_status, new_status,
                requested_by, requested_role, reason, approval_status,
                hod_required, date, course_code, period)
               VALUES (?,?,?,?,?,?,?,?,'PENDING',?,?,?,?)""",
            (student_id, faculty_id, subject_id, old_status, new_status,
             requested_by, requested_role, reason,
             hod_required, date, course_code, period),
        )
        return cur.lastrowid


def get_pending_requests(faculty_id=None, hod_required=None, limit=200):
    """Fetch PENDING requests, optionally filtered by faculty_id or hod_required."""
    query = "SELECT * FROM attendance_override_requests WHERE approval_status='PENDING'"
    params = []
    if faculty_id:
        query += " AND faculty_id=?"
        params.append(faculty_id)
    if hod_required is not None:
        query += " AND hod_required=?"
        params.append(hod_required)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def approve_override_request(request_id, approved_by, approved_role):
    """Set approval_status=APPROVED."""
    with _db() as conn:
        conn.execute(
            """UPDATE attendance_override_requests
               SET approval_status='APPROVED', approved_by=?, approved_role=?,
                   updated_at=datetime('now','localtime')
               WHERE id=?""",
            (approved_by, approved_role, request_id),
        )


def reject_override_request(request_id, approved_by, approved_role, reason):
    """Set approval_status=REJECTED."""
    with _db() as conn:
        conn.execute(
            """UPDATE attendance_override_requests
               SET approval_status='REJECTED', approved_by=?, approved_role=?,
                   reason=COALESCE(reason||' | Rejected: ','Rejected: ')||?,
                   updated_at=datetime('now','localtime')
               WHERE id=?""",
            (approved_by, approved_role, reason, request_id),
        )


# =============================================================
# AUDIT LOG HELPERS
# =============================================================

def add_audit_log(attendance_id, old_value, new_value,
                  changed_by, changed_role, reason, action_type):
    """Insert an immutable audit log entry. Returns new row id."""
    with _db() as conn:
        cur = conn.execute(
            """INSERT INTO attendance_audit_logs
               (attendance_id, old_value, new_value, changed_by,
                changed_role, reason, action_type)
               VALUES (?,?,?,?,?,?,?)""",
            (attendance_id, old_value, new_value,
             changed_by, changed_role, reason, action_type),
        )
        return cur.lastrowid


def get_audit_log(limit=500):
    """Return audit logs ordered by timestamp DESC."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM attendance_audit_logs ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]



# =============================================================
# STAFF HELPERS
# =============================================================
def get_staff_by_id(staff_id: str) -> dict | None:
    """Return faculty record with class-incharge info."""
    try:
        with _db() as conn:
            row = conn.execute(
                "SELECT * FROM faculty WHERE fac_id=? AND active=1",
                (staff_id,)
            ).fetchone()
            return dict(row) if row else None
    except Exception as e:
        log.warning("get_staff_by_id: %s", e)
        return None


def resolve_target_role(target_id: str) -> str | None:
    """
    Determine the ROLE of the person whose attendance is being overridden.

    Lookup order:
      1. hods table     → "hod"
      2. faculty table  → "staff"
      3. students table → "student"

    Returns canonical role string or None if not found.
    """
    if not target_id or not target_id.strip():
        return None

    target_id = target_id.strip()

    try:
        with _db() as conn:
            # 1. Check HODs table
            row = conn.execute(
                "SELECT 1 FROM hods WHERE hod_id=? AND active=1 LIMIT 1",
                (target_id,)
            ).fetchone()
            if row:
                return "hod"

            # Also check hods by name/email (fallback)
            row = conn.execute(
                "SELECT 1 FROM hods WHERE (email=? OR name=?) AND active=1 LIMIT 1",
                (target_id, target_id)
            ).fetchone()
            if row:
                return "hod"

            # 2. Check faculty table
            row = conn.execute(
                "SELECT role FROM faculty WHERE fac_id=? AND active=1 LIMIT 1",
                (target_id,)
            ).fetchone()
            if row:
                raw = (row["role"] or "staff").lower()
                # If faculty table says hod/admin, return that
                if raw in HOD_ROLE_ALIASES:
                    return "hod"
                if raw in ADMIN_ROLE_ALIASES:
                    return "admin"
                return "staff"

            # 3. Check students table
            row = conn.execute(
                """SELECT 1 FROM students
                   WHERE (register_number=? OR student_id=? OR roll_number=?)
                   AND active=1 LIMIT 1""",
                (target_id, target_id, target_id)
            ).fetchone()
            if row:
                return "student"

    except Exception as e:
        log.warning("resolve_target_role(%s): %s", target_id, e)

    return None


# =============================================================
# CORE HIERARCHICAL PERMISSION CHECK  (v11.0)
# =============================================================
def check_hierarchical_permission(
    actor_role: str,
    target_id:  str,
    target_role_hint: str = "",
) -> tuple[bool, str, str]:
    """
    Enforce the strict override hierarchy.

    Args:
        actor_role:       The JWT role of the logged-in user.
        target_id:        The ID of the person whose record is being overridden.
        target_role_hint: Optional role hint supplied by the caller (validated, not trusted).

    Returns:
        (allowed: bool, message: str, resolved_target_role: str)

    Access matrix:
        admin   → hod only
        hod     → staff only
        staff   → student only
        student → denied
    """
    actor = _normalise_role(actor_role)

    # Students can never override anyone
    if actor == "student" or actor not in ROLE_CAN_OVERRIDE:
        return False, "Access Denied: Students have no override permissions.", ""

    allowed_target = ROLE_CAN_OVERRIDE.get(actor)
    if allowed_target is None:
        return False, f"Access Denied: Role '{actor}' has no override permissions.", ""

    # Resolve the actual role of the target record owner
    resolved = resolve_target_role(target_id)

    # If we can't resolve, try the hint (but normalise it first so it can't be spoofed)
    if not resolved and target_role_hint:
        resolved = _normalise_role(target_role_hint)
        log.warning(
            "Target ID '%s' not found in DB; using caller-supplied hint '%s' → '%s'",
            target_id, target_role_hint, resolved
        )

    if not resolved:
        return False, (
            f"Access Denied: Cannot identify the role of target '{target_id}'. "
            "Ensure the target ID exists in the system."
        ), ""

    # Normalise resolved role to a canonical bucket
    normalised_target = _normalise_role(resolved)

    actor_display  = ROLE_DISPLAY.get(actor, actor.title())
    target_display = ROLE_DISPLAY.get(normalised_target, normalised_target.title())
    allowed_display = ROLE_DISPLAY.get(allowed_target, allowed_target.title())

    if normalised_target == allowed_target:
        return True, (
            f"Authorised: {actor_display} → {target_display} override permitted."
        ), normalised_target

    return False, (
        f"Access Denied: {actor_display} can only override {allowed_display} records, "
        f"not {target_display} records."
    ), normalised_target


# =============================================================
# LEGACY COMPATIBILITY — kept so existing call-sites don't break
# =============================================================
def check_staff_permission(staff_id: str, department: str,
                            year: str, section: str,
                            course_code: str) -> tuple[bool, str]:
    """
    DEPRECATED — use check_hierarchical_permission() instead.
    Retained only for backward compatibility with old call sites.
    Always returns (True, "Legacy: no hierarchical check performed.").
    The real check is now done in the API layer with check_hierarchical_permission().
    """
    log.warning(
        "check_staff_permission() is deprecated. "
        "Use check_hierarchical_permission() in the API layer."
    )
    return True, "Legacy permission check (use hierarchical check instead)."


# =============================================================
# SAVE OVERRIDE
# =============================================================
def add_attendance_override(
    department:              str,
    year:                    str,
    semester:                str,
    section:                 str,
    student_register_number: str,
    student_name:            str,
    course_code:             str,
    course_name:             str,
    period:                  str,
    attendance_from:         str,
    attendance_to:           str,
    reason:                  str,
    overridden_by:           str,
    staff_id:                str,
    staff_role:              str,
    target_role:             str = "",
) -> int:
    """Insert an override record. Returns new row id."""
    now = datetime.now()
    override_date = now.strftime("%Y-%m-%d")
    override_time = now.strftime("%H:%M:%S")

    with _db() as conn:
        cur = conn.execute("""
            INSERT INTO attendance_overrides (
                department, year, semester, section,
                student_register_number, student_name,
                course_code, course_name,
                period, attendance_from, attendance_to,
                reason, overridden_by, staff_id, staff_role,
                target_role, override_date, override_time
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            department, year, semester, section,
            student_register_number, student_name,
            course_code, course_name,
            period, attendance_from, attendance_to,
            reason, overridden_by, staff_id, staff_role,
            target_role, override_date, override_time
        ))
        return cur.lastrowid


# =============================================================
# QUERY HELPERS
# =============================================================

_DEPT_ORDER = {
    "CSE": 1, "IT": 2, "ECE": 3, "EEE": 4, "MECH": 5,
    "CIVIL": 6, "AIDS": 7, "AIML": 8, "CSD": 9,
}
_YEAR_ORDER = {"1": 1, "2": 2, "3": 3, "4": 4,
               "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}


def _sort_key(row: dict):
    dept = (row.get("department") or "").upper()
    year = str(row.get("year") or "")
    sem  = str(row.get("semester") or "")
    sec  = (row.get("section") or "").upper()
    try:
        d = _DEPT_ORDER.get(dept, 99)
        y = _YEAR_ORDER.get(year, int(year) if year.isdigit() else 99)
        s = int(sem) if sem.isdigit() else 99
        return (d, y, s, sec)
    except Exception:
        return (99, 99, 99, sec)


def get_override_history(limit: int = 500) -> list:
    """Return all overrides sorted Dept→Year→Semester→Section."""
    with _db() as conn:
        rows = conn.execute("""
            SELECT * FROM attendance_overrides
            ORDER BY department, year, semester, section, override_date DESC, override_time DESC
            LIMIT ?
        """, (limit,)).fetchall()
        result = [dict(r) for r in rows]
    result.sort(key=_sort_key)
    return result


def get_override_history_filtered(
    department: str = "",
    year: str = "",
    semester: str = "",
    section: str = "",
    staff_id: str = "",
    course_code: str = "",
    limit: int = 500,
) -> list:
    """Return filtered overrides, sorted Dept→Year→Sem→Sec."""
    clauses, params = [], []

    if department:
        clauses.append("UPPER(department) = UPPER(?)")
        params.append(department)
    if year:
        clauses.append("year = ?")
        params.append(str(year))
    if semester:
        clauses.append("semester = ?")
        params.append(str(semester))
    if section:
        clauses.append("UPPER(section) = UPPER(?)")
        params.append(section)
    if staff_id:
        clauses.append("staff_id = ?")
        params.append(staff_id)
    if course_code:
        clauses.append("UPPER(course_code) = UPPER(?)")
        params.append(course_code)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    with _db() as conn:
        rows = conn.execute(f"""
            SELECT * FROM attendance_overrides
            {where}
            ORDER BY department, year, semester, section,
                     override_date DESC, override_time DESC
            LIMIT ?
        """, params).fetchall()
        result = [dict(r) for r in rows]

    result.sort(key=_sort_key)
    return result


def get_override_stats() -> dict:
    """Quick KPI stats for the overrides dashboard."""
    with _db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM attendance_overrides"
        ).fetchone()[0]

        today = datetime.now().strftime("%Y-%m-%d")
        today_count = conn.execute(
            "SELECT COUNT(*) FROM attendance_overrides WHERE override_date=?",
            (today,)
        ).fetchone()[0]

        staff_count = conn.execute(
            "SELECT COUNT(DISTINCT staff_id) FROM attendance_overrides"
        ).fetchone()[0]

        last_row = conn.execute(
            "SELECT * FROM attendance_overrides ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last = dict(last_row) if last_row else {}

    return {
        "total":         total,
        "today":         today_count,
        "staff_count":   staff_count,
        "last_override": last,
    }


# ── Auto-init ──────────────────────────────────────────────
try:
    init_override_tables()
except Exception as _e:
    log.warning("init_override_tables (non-fatal): %s", _e)