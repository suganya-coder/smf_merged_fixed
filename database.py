
    
    
    # =============================================================
# database.py  —  Smart Attendance System  v9.0  (SQLite)
#
# FIXES v9.0:
#  - Added get_dashboard_stats() — correct dashboard counts using
#    COUNT(DISTINCT student_id) and UPPER(status) = 'PRESENT'
#  - Fixed get_attendance_summary() — filters UPPER(status) = 'PRESENT'
#    so absent/late period rows are excluded from present-day counts
#  - All existing functions preserved unchanged
# =============================================================
import os, time, sqlite3, logging
from datetime import datetime, timedelta
from contextlib import contextmanager
import config

log = logging.getLogger(__name__)

DB_PATH = os.path.join(config.BASE_DIR, "attendance.db")


@contextmanager
def db():
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


# =============================================================
# INIT / MIGRATION
# =============================================================
def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS students (
            student_id      TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            register_number TEXT UNIQUE,
            roll_number     TEXT,
            first_name      TEXT,
            last_name       TEXT,
            gender          TEXT,
            date_of_birth   TEXT,
            department      TEXT,
            course          TEXT,
            year            TEXT,
            section         TEXT,
            student_email   TEXT,
            parent_email    TEXT,
            student_mobile  TEXT,
            parent_mobile   TEXT,
            status          TEXT DEFAULT 'Active',
            twin_of         TEXT,
            consent         INTEGER DEFAULT 1,
            enrolled_on     TEXT,
            active          INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS attendance (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id     TEXT NOT NULL,
            name           TEXT,
            period         TEXT,
            date           TEXT,
            time           TEXT,
            confidence     REAL DEFAULT 0,
            engine         TEXT,
            camera_id      TEXT DEFAULT 'CAM1',
            liveness_score REAL DEFAULT 0,
            twin_verified  INTEGER DEFAULT 0,
            skeleton_score REAL DEFAULT 0,
            UNIQUE(student_id, period, date)
        );

        CREATE TABLE IF NOT EXISTS snapshot_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT,
            period     TEXT,
            date       TEXT,
            count      INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS override_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT,
            period     TEXT,
            action     TEXT,
            note       TEXT,
            teacher    TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS timetable (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            period_name TEXT,
            start_time  TEXT,
            end_time    TEXT,
            active      INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name  TEXT,
            action     TEXT,
            resource   TEXT,
            detail     TEXT,
            ip_address TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS twin_analysis_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id       TEXT,
            name             TEXT,
            twin_id          TEXT,
            verified         INTEGER DEFAULT 0,
            confidence       REAL DEFAULT 0,
            method           TEXT,
            period           TEXT,
            iris_score       REAL DEFAULT 0,
            skeleton_score   REAL DEFAULT 0,
            periocular_score REAL DEFAULT 0,
            geometry_score   REAL DEFAULT 0,
            final_confidence REAL DEFAULT 0,
            decision         TEXT,
            feature_vector   TEXT,
            timestamp        TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_att_date    ON attendance(date);
        CREATE INDEX IF NOT EXISTS idx_att_student ON attendance(student_id);
        CREATE INDEX IF NOT EXISTS idx_att_period  ON attendance(period);

        -- ── Role-based attendance tables (v10.3) ──────────────────────────
        CREATE TABLE IF NOT EXISTS student_attendance (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id  TEXT NOT NULL,
            name        TEXT,
            role        TEXT DEFAULT 'Student',
            department  TEXT,
            course      TEXT DEFAULT '',
            year        TEXT DEFAULT '',
            section     TEXT DEFAULT '',
            semester    TEXT DEFAULT '',
            subject_id  TEXT DEFAULT '',
            date        TEXT,
            time        TEXT,
            period      TEXT,
            status      TEXT DEFAULT 'Present',
            confidence  REAL DEFAULT 0,
            UNIQUE(student_id, period, date)
        );

        CREATE TABLE IF NOT EXISTS staff_attendance (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id   TEXT NOT NULL,
            name       TEXT,
            role       TEXT DEFAULT 'Staff',
            department TEXT,
            date       TEXT,
            time       TEXT,
            period     TEXT,
            status     TEXT DEFAULT 'Present',
            confidence REAL DEFAULT 0,
            UNIQUE(staff_id, period, date)
        );

        CREATE INDEX IF NOT EXISTS idx_staffatt_date   ON staff_attendance(date);
        CREATE INDEX IF NOT EXISTS idx_staffatt_dept   ON staff_attendance(department, date);

        CREATE TABLE IF NOT EXISTS hod_attendance (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            hod_id     TEXT NOT NULL,
            name       TEXT,
            role       TEXT DEFAULT 'HOD',
            department TEXT,
            date       TEXT,
            time       TEXT,
            period     TEXT,
            status     TEXT DEFAULT 'Present',
            confidence REAL DEFAULT 0,
            UNIQUE(hod_id, period, date)
        );

        CREATE INDEX IF NOT EXISTS idx_hodatt_date   ON hod_attendance(date);
        CREATE INDEX IF NOT EXISTS idx_hodatt_dept   ON hod_attendance(department, date);
        """)

        # Migrate: add missing columns if upgrading from older DB
        _safe_add_column(conn, "twin_analysis_log", "iris_score",       "REAL DEFAULT 0")
        _safe_add_column(conn, "twin_analysis_log", "skeleton_score",   "REAL DEFAULT 0")
        _safe_add_column(conn, "twin_analysis_log", "periocular_score", "REAL DEFAULT 0")
        _safe_add_column(conn, "twin_analysis_log", "geometry_score",   "REAL DEFAULT 0")
        _safe_add_column(conn, "twin_analysis_log", "final_confidence", "REAL DEFAULT 0")
        _safe_add_column(conn, "twin_analysis_log", "decision",         "TEXT")
        _safe_add_column(conn, "twin_analysis_log", "feature_vector",   "TEXT")

        # Migrate student_attendance: add new columns for existing implicit tables
        # (deployments where SQLite created the table via the first INSERT)
        _safe_add_column(conn, "student_attendance", "course",      "TEXT DEFAULT ''")
        _safe_add_column(conn, "student_attendance", "year",        "TEXT DEFAULT ''")
        _safe_add_column(conn, "student_attendance", "section",     "TEXT DEFAULT ''")
        _safe_add_column(conn, "student_attendance", "semester",    "TEXT DEFAULT ''")
        _safe_add_column(conn, "student_attendance", "subject_id",  "TEXT DEFAULT ''")
        _safe_add_column(conn, "student_attendance", "role",        "TEXT DEFAULT 'Student'")
        _safe_add_column(conn, "student_attendance", "status",      "TEXT DEFAULT 'Present'")

        # Create student_attendance indexes AFTER _safe_add_column so the section
        # column is guaranteed to exist on legacy DBs before the index is built.
        for _idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_sa_date    ON student_attendance(date)",
            "CREATE INDEX IF NOT EXISTS idx_sa_student ON student_attendance(student_id)",
            "CREATE INDEX IF NOT EXISTS idx_sa_section ON student_attendance(section, date)",
            "CREATE INDEX IF NOT EXISTS idx_sa_dept    ON student_attendance(department, date)",
        ]:
            try:
                conn.execute(_idx_sql)
            except sqlite3.OperationalError:
                pass

        # Migrate students table: add new fields if upgrading from older DB
        _safe_add_column(conn, "students", "register_number", "TEXT")
        _safe_add_column(conn, "students", "first_name",      "TEXT")
        _safe_add_column(conn, "students", "last_name",       "TEXT")
        _safe_add_column(conn, "students", "gender",          "TEXT")
        _safe_add_column(conn, "students", "date_of_birth",   "TEXT")
        _safe_add_column(conn, "students", "department",      "TEXT")
        _safe_add_column(conn, "students", "course",          "TEXT")
        _safe_add_column(conn, "students", "year",            "TEXT")
        _safe_add_column(conn, "students", "student_email",   "TEXT")
        _safe_add_column(conn, "students", "parent_email",    "TEXT")
        _safe_add_column(conn, "students", "student_mobile",  "TEXT")
        _safe_add_column(conn, "students", "parent_mobile",   "TEXT")
        _safe_add_column(conn, "students", "status",          "TEXT DEFAULT 'Active'")

        # Email OTP verification fields (v9.7 — enrollment OTP)
        _safe_add_column(conn, "students", "email_verified",  "INTEGER DEFAULT 0")
        _safe_add_column(conn, "students", "otp_hash",        "TEXT")
        _safe_add_column(conn, "students", "otp_expiry",      "TEXT")
        _safe_add_column(conn, "students", "otp_attempts",    "INTEGER DEFAULT 0")
        _safe_add_column(conn, "students", "otp_resend_count","INTEGER DEFAULT 0")

        # Seed timetable if empty
        row = conn.execute("SELECT COUNT(*) FROM timetable").fetchone()[0]
        if row == 0:
            for p in config.DEFAULT_PERIODS:
                conn.execute(
                    "INSERT INTO timetable(period_name,start_time,end_time) VALUES(?,?,?)",
                    (p["name"], p["start"], p["end"])
                )

        # ── Unified faculty table — replaces the old staff table ──────────
        conn.execute("""
        CREATE TABLE IF NOT EXISTS faculty (
            fac_id               TEXT PRIMARY KEY,
            name                 TEXT NOT NULL DEFAULT '',
            gender               TEXT DEFAULT '',
            dept                 TEXT DEFAULT '',
            department           TEXT DEFAULT '',
            designation          TEXT DEFAULT 'Assistant Professor',
            email                TEXT DEFAULT '',
            mobile               TEXT DEFAULT '',
            specialization       TEXT DEFAULT '',
            qualification        TEXT DEFAULT '',
            date_of_birth        TEXT DEFAULT '',
            dob                  TEXT DEFAULT '',
            password_hash        TEXT DEFAULT '',
            active               INTEGER DEFAULT 1,
            created_at           TEXT DEFAULT (datetime('now','localtime')),
            is_class_incharge    INTEGER DEFAULT 0,
            incharge_department  TEXT DEFAULT '',
            incharge_year        TEXT DEFAULT '',
            incharge_section     TEXT DEFAULT '',
            role                 TEXT DEFAULT 'Faculty',
            class_incharge_dept  TEXT DEFAULT '',
            class_incharge_year  INTEGER DEFAULT 0,
            class_incharge_section TEXT DEFAULT '',
            subjects             TEXT DEFAULT '[]',
            first_name           TEXT DEFAULT '',
            last_name            TEXT DEFAULT '',
            employee_code        TEXT DEFAULT '',
            joining_date         TEXT DEFAULT '',
            status               TEXT DEFAULT 'Active',
            enrolled_on          TEXT DEFAULT ''
        );
        """)
        for _col, _defn in [
            ("first_name",     "TEXT DEFAULT ''"),
            ("last_name",      "TEXT DEFAULT ''"),
            ("department",     "TEXT DEFAULT ''"),
            ("employee_code",  "TEXT DEFAULT ''"),
            ("joining_date",   "TEXT DEFAULT ''"),
            ("enrolled_on",    "TEXT DEFAULT ''"),
            ("status",         "TEXT DEFAULT 'Active'"),
            ("dob",            "TEXT DEFAULT ''"),
            ("subjects",       "TEXT DEFAULT '[]'"),
            ("password",       "TEXT DEFAULT 'Staff@123'"),
        ]:
            _safe_add_column(conn, "faculty", _col, _defn)
        # Ensure all faculty have the new password set
        conn.execute("""
            UPDATE faculty
            SET password='Staff@123'
            WHERE password IS NULL OR password=''
        """)
        try:
            conn.execute("""
                INSERT OR IGNORE INTO faculty
                    (fac_id, name, gender, dept, department, designation,
                     email, mobile, date_of_birth, dob, role, active,
                     first_name, last_name, employee_code,
                     joining_date, status, enrolled_on)
                SELECT
                    staff_id,
                    TRIM(COALESCE(first_name,'')||' '||COALESCE(last_name,'')),
                    gender, department, department, designation,
                    email, mobile, date_of_birth, date_of_birth,
                    COALESCE(role,'Faculty'), active,
                    first_name, COALESCE(last_name,''), COALESCE(employee_code,''),
                    COALESCE(joining_date,''), COALESCE(status,'Active'),
                    COALESCE(enrolled_on,'')
                FROM staff
            """)
            conn.execute("DROP TABLE IF EXISTS staff")
            log.info("Legacy staff table migrated and dropped.")
        except Exception:
            pass

        # HOD table
        conn.execute("""
        CREATE TABLE IF NOT EXISTS hods (
            hod_id          TEXT PRIMARY KEY,
            name            TEXT NOT NULL DEFAULT '',
            dept            TEXT NOT NULL DEFAULT '',
            designation     TEXT NOT NULL DEFAULT 'Head of Department',
            email           TEXT,
            mobile          TEXT,
            password        TEXT DEFAULT 'Hod@123',
            joined_on       TEXT,
            active          INTEGER DEFAULT 1,
            employee_code   TEXT DEFAULT '',
            first_name      TEXT DEFAULT '',
            last_name       TEXT DEFAULT '',
            gender          TEXT DEFAULT '',
            date_of_birth   TEXT DEFAULT '',
            joining_date    TEXT DEFAULT '',
            status          TEXT DEFAULT 'Active',
            enrolled_on     TEXT DEFAULT '',
            role            TEXT DEFAULT 'HOD'
        );
        """)
        for _col, _defn in [
            ("employee_code", "TEXT DEFAULT ''"),
            ("first_name",    "TEXT DEFAULT ''"),
            ("last_name",     "TEXT DEFAULT ''"),
            ("gender",        "TEXT DEFAULT ''"),
            ("date_of_birth", "TEXT DEFAULT ''"),
            ("joining_date",  "TEXT DEFAULT ''"),
            ("status",        "TEXT DEFAULT 'Active'"),
            ("enrolled_on",   "TEXT DEFAULT ''"),
            ("role",          "TEXT DEFAULT 'HOD'"),
            ("password",      "TEXT DEFAULT 'Hod@123'"),
        ]:
            _safe_add_column(conn, "hods", _col, _defn)
        # Ensure all HODs have the new password set
        conn.execute("""
            UPDATE hods
            SET password='Hod@123'
            WHERE password IS NULL OR password=''
        """)

        # ── FIX: repair any HODs stuck at active=0 due to legacy/migrated data ──
        # A HOD record that exists (was enrolled or seeded) should always be
        # visible.  The only legitimate active=0 state is an explicit soft-delete
        # performed through the admin UI (deactivate_hod()).  Pre-existing DBs
        # shipped with active=0 rows would otherwise cause "No HODs found" on every
        # fresh deployment, so we restore them here unconditionally on startup.
        conn.execute("UPDATE hods SET active=1 WHERE active IS NULL OR active=0")

        # ── FIX: sync student active/status columns on startup ───────────────
        # Root cause: old PATCH /deactivate only set status='Inactive' (not
        # active=0), and old PUT update hardcoded status='Active' without
        # touching active.  This left rows where status='Active' AND active=0
        # that were hidden from get_all_students() yet still rendered via the
        # student_attendance fallback — causing "Deactivate failed: Not Found".
        # Align both columns on every startup so the DB self-heals.
        conn.execute("""
            UPDATE students
            SET active=0
            WHERE status='Inactive' AND (active IS NULL OR active != 0)
        """)
        conn.execute("""
            UPDATE students
            SET active=1
            WHERE status='Active' AND (active IS NULL OR active != 1)
        """)

        # ── F-04: brute-force lockout — faculty columns ───────────────────────
        _safe_add_column(conn, "faculty", "failed_attempts", "INTEGER DEFAULT 0")
        _safe_add_column(conn, "faculty", "is_locked",       "INTEGER DEFAULT 0")
        _safe_add_column(conn, "faculty", "lock_until",      "TEXT")

        # ── F-04: brute-force lockout — admin/HOD lockout table ──────────────
        conn.execute("""
        CREATE TABLE IF NOT EXISTS login_lockout (
            identifier      TEXT PRIMARY KEY,
            failed_attempts INTEGER DEFAULT 0,
            is_locked       INTEGER DEFAULT 0,
            lock_until      TEXT
        )
        """)

        # ── F-11: JWT token blocklist (logout / revocation) ───────────────────
        # jti is the unique token ID stamped into every JWT at creation (Step 5).
        # Rows self-expire: cleanup_expired_tokens() prunes them once the token's
        # own exp window passes, so the table never grows unboundedly.
        conn.execute("""
        CREATE TABLE IF NOT EXISTS token_blocklist (
            jti        TEXT PRIMARY KEY,
            blocked_at TEXT DEFAULT (datetime('now','utc')),
            expires_at TEXT NOT NULL
        )
        """)

        # ── F-13: Token-version table (session invalidation on pw change) ────
        # Increment the version for a user whenever their password is reset or
        # changed.  Tokens minted before the increment carry the old version and
        # are rejected by decode_token() even if they have not yet expired.
        conn.execute("""
        CREATE TABLE IF NOT EXISTS user_token_version (
            identifier TEXT PRIMARY KEY,
            version    INTEGER DEFAULT 0,
            updated_at TEXT    DEFAULT (datetime('now','utc'))
        )
        """)

        # ── F-14: Password history table (reuse prevention) ───────────────────
        # Stores the last N bcrypt hashes per identifier so that reset_password()
        # and change_password() can reject any password the user has used before.
        # Only hashes are stored — plain-text passwords never touch this table.
        # Rows beyond the last 5 per identifier are pruned by add_password_history().
        conn.execute("""
        CREATE TABLE IF NOT EXISTS password_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            identifier TEXT    NOT NULL,
            pwd_hash   TEXT    NOT NULL,
            created_at TEXT    DEFAULT (datetime('now','utc'))
        )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pwdhist_id "
            "ON password_history(identifier, created_at)"
        )

        # ── FIX: normalise common dept typos introduced by migration scripts ──
        # migrate_hod_to_hods.py copied 'department' from the legacy table which
        # often stored 'CS' / 'cse' instead of the canonical 'CSE' the frontend
        # dropdown uses.  Normalise once on startup; safe to re-run.
        _DEPT_NORM = [
            ("CS",               "CSE"),
            ("Computer Science", "CSE"),
            ("cse",              "CSE"),
            ("Ece",              "ECE"),
            ("Electronics",      "ECE"),
            ("Mech",             "MECH"),
            ("Mechanical",       "MECH"),
            ("It",               "IT"),
            ("Information Technology", "IT"),
            ("Eee",              "EEE"),
            ("Electrical",       "EEE"),
            ("Civil",            "CIVIL"),
        ]
        for wrong, right in _DEPT_NORM:
            conn.execute(
                "UPDATE hods SET dept=? WHERE dept=?", (right, wrong)
            )

    log.info("SQLite DB ready at %s", DB_PATH)
    print(f"  DB: {DB_PATH}")
    _patch_faculty_dobs()


def _safe_add_column(conn, table, column, definition):
    """Add column if it doesn't exist (migration helper)."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError:
        pass  # Column already exists


# DOB lookup for all 50 seeded faculty (migration helper)
_FACULTY_DOB_MAP = {
    "FAC001": "1972-04-15", "FAC002": "1978-08-22", "FAC003": "1984-03-10",
    "FAC004": "1986-11-05", "FAC005": "1989-06-18", "FAC006": "1987-01-30",
    "FAC007": "1990-09-14", "FAC008": "1970-02-28", "FAC009": "1976-07-12",
    "FAC010": "1983-05-25", "FAC011": "1985-10-08", "FAC012": "1988-12-20",
    "FAC013": "1991-03-03", "FAC014": "1979-09-17", "FAC015": "1984-06-06",
    "FAC016": "1987-04-22", "FAC017": "1973-11-11", "FAC018": "1977-08-30",
    "FAC019": "1985-02-14", "FAC020": "1988-07-07", "FAC021": "1990-01-19",
    "FAC022": "1980-05-05", "FAC023": "1986-09-23", "FAC024": "1989-12-01",
    "FAC025": "1975-03-16", "FAC026": "1986-08-12", "FAC027": "1988-04-27",
    "FAC028": "1991-10-15", "FAC029": "1987-06-09", "FAC030": "1989-02-20",
    "FAC031": "1990-11-04", "FAC032": "1985-07-18", "FAC033": "1988-01-31",
    "FAC034": "1989-05-22", "FAC035": "1991-09-13", "FAC036": "1987-03-07",
    "FAC037": "1990-08-24", "FAC038": "1986-12-16", "FAC039": "1989-04-03",
    "FAC040": "1981-06-28", "FAC041": "1984-10-10", "FAC042": "1988-07-01",
    "FAC043": "1978-02-14", "FAC044": "1985-05-19", "FAC045": "1988-11-26",
    "FAC046": "1990-03-08", "FAC047": "1987-09-15", "FAC048": "1989-01-22",
    "FAC049": "1986-06-30", "FAC050": "1991-08-05",
}


def _patch_faculty_dobs():
    """
    One-time migration: fill empty dob for all seeded faculty rows.
    Safe to run repeatedly — only updates rows where dob is NULL or empty.
    """
    try:
        with db() as conn:
            _safe_add_column(conn, "faculty", "dob", "TEXT DEFAULT ''")
            patched = 0
            for fac_id, dob in _FACULTY_DOB_MAP.items():
                r = conn.execute(
                    "UPDATE faculty SET dob=? WHERE fac_id=? AND (dob IS NULL OR dob='')",
                    (dob, fac_id)
                )
                patched += r.rowcount
            if patched:
                log.info("Faculty DOB migration: patched %d rows", patched)
                print(f"  Faculty DOB migration: {patched} rows updated.")
    except Exception as e:
        log.warning("Faculty DOB patch skipped: %s", e)


# =============================================================
# STUDENTS
# =============================================================
def get_all_students():
    """Return every student row regardless of active flag, ordered by name.

    FIX: Previously filtered WHERE active=1 and fell back to student_attendance
    when no active=1 rows existed.  That fallback returned student_id values
    from the attendance log that had no matching row in the students table,
    causing the deactivate endpoint to return 404 for every student rendered
    from the fallback.

    Returning all rows (active=0 included) means:
      - Every student_id the UI renders is guaranteed to exist in students.
      - The frontend reads s.status to colour the badge; active=0 students
        show as Inactive correctly via the status column.
      - The student_attendance fallback is retained only when the students
        table is truly empty (legacy DB with no students rows at all).
    """
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM students ORDER BY name"
        ).fetchall()
        if rows:
            return [dict(r) for r in rows]
        # Fallback: only reached on a legacy DB where the students table has
        # zero rows at all.  These synthetic rows have no students entry so
        # the frontend should not offer deactivate/edit actions for them.
        try:
            rows = conn.execute("""
                SELECT DISTINCT student_id,
                       name,
                       department,
                       '' AS roll_number,
                       '' AS section,
                       '' AS register_number,
                       '' AS course,
                       '' AS year,
                       '' AS student_email,
                       '' AS parent_email,
                       '' AS student_mobile,
                       '' AS parent_mobile,
                       'student' AS role,
                       'Active' AS status,
                       1 AS active
                FROM student_attendance
                ORDER BY name
            """).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []


def get_student(student_id: str):
    """Get a single student by ID. Returns dict or None."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM students WHERE student_id=? AND active=1",
            (student_id,)
        ).fetchone()
        return dict(row) if row else None


def get_faculty_by_id(fac_id: str):
    """Get a faculty member by fac_id from the faculty table. Returns dict or None."""
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT * FROM faculty WHERE fac_id=? AND active=1",
                (fac_id,)
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


# =============================================================
# PASSWORD HELPERS  (F-02 bcrypt fix — do NOT edit auth_utils.py)
# =============================================================

def update_faculty_password(fac_id: str, hashed_password: str) -> bool:
    """Persist a bcrypt-hashed password for a faculty row.

    Called by:
      - api.py api_login() lazy-migration path (plain-text → bcrypt on first login)
      - migrate_plaintext_passwords() bulk startup migration

    Args:
        fac_id:          Primary key of the faculty row.
        hashed_password: bcrypt hash string (starts with '$2b$').

    Returns True on success, False on any DB error.
    """
    try:
        with db() as conn:
            conn.execute(
                "UPDATE faculty SET password=? WHERE fac_id=?",
                (hashed_password, fac_id),
            )
        return True
    except Exception as _e:
        log.error("update_faculty_password(%s): %s", fac_id, _e)
        return False


def update_hod_password(hod_id: str, hashed_password: str) -> bool:
    """Persist a bcrypt-hashed password for a HOD row.

    Called by:
      - api.py api_login() lazy-migration path for HOD logins
      - migrate_plaintext_passwords() bulk startup migration

    Args:
        hod_id:          Primary key of the hods row.
        hashed_password: bcrypt hash string (starts with '$2b$').

    Returns True on success, False on any DB error.
    """
    try:
        with db() as conn:
            conn.execute(
                "UPDATE hods SET password=? WHERE hod_id=?",
                (hashed_password, hod_id),
            )
        return True
    except Exception as _e:
        log.error("update_hod_password(%s): %s", hod_id, _e)
        return False


def migrate_plaintext_passwords() -> int:
    """One-time startup migration: bcrypt-hash every plain-text password.

    Scans both the ``faculty`` and ``hods`` tables for rows whose password
    column does NOT begin with the bcrypt sentinel ``$2b$``.  Each such row
    is hashed with bcrypt and updated in-place.

    Safe to call on every startup — already-hashed rows are skipped
    instantly (the ``NOT LIKE '$2b$%'`` filter excludes them).

    Returns the total number of rows migrated (0 = everything already hashed).
    """
    try:
        from auth_utils import hash_password as _hp
    except Exception as _imp_err:
        log.error("migrate_plaintext_passwords: cannot import hash_password — %s", _imp_err)
        return 0

    migrated = 0

    # ── Faculty ───────────────────────────────────────────────
    try:
        with db() as conn:
            plain_faculty = conn.execute(
                "SELECT fac_id, password FROM faculty "
                "WHERE password IS NOT NULL AND password != '' "
                "AND password NOT LIKE '$2b$%'"
            ).fetchall()

        for row in plain_faculty:
            fac_id   = row["fac_id"]
            plain_pw = row["password"]
            try:
                new_hash = _hp(plain_pw)
                if update_faculty_password(fac_id, new_hash):
                    migrated += 1
                    log.info("migrate_plaintext_passwords: hashed faculty %s", fac_id)
            except Exception as _row_err:
                log.warning("migrate_plaintext_passwords: skipped faculty %s — %s",
                            fac_id, _row_err)
    except Exception as _fac_err:
        log.error("migrate_plaintext_passwords (faculty): %s", _fac_err)

    # ── HODs ──────────────────────────────────────────────────
    try:
        with db() as conn:
            plain_hods = conn.execute(
                "SELECT hod_id, password FROM hods "
                "WHERE password IS NOT NULL AND password != '' "
                "AND password NOT LIKE '$2b$%'"
            ).fetchall()

        for row in plain_hods:
            hod_id   = row["hod_id"]
            plain_pw = row["password"]
            try:
                new_hash = _hp(plain_pw)
                if update_hod_password(hod_id, new_hash):
                    migrated += 1
                    log.info("migrate_plaintext_passwords: hashed HOD %s", hod_id)
            except Exception as _row_err:
                log.warning("migrate_plaintext_passwords: skipped HOD %s — %s",
                            hod_id, _row_err)
    except Exception as _hod_err:
        log.error("migrate_plaintext_passwords (hods): %s", _hod_err)

    if migrated:
        log.info("migrate_plaintext_passwords: migrated %d row(s) to bcrypt.", migrated)
    else:
        log.debug("migrate_plaintext_passwords: all passwords already hashed — nothing to do.")

    return migrated


# =============================================================
# F-04: BRUTE-FORCE LOGIN LOCKOUT HELPERS
# =============================================================
_LOCKOUT_MAX_ATTEMPTS = 5
_LOCKOUT_DURATION_MIN = 15


def get_login_fail_count(identifier: str):
    """Return (failed_attempts, is_locked, lock_until) for a fac_id or email.

    Looks up the faculty table for identifiers that look like a fac_id
    (non-empty and not an email address); falls back to the login_lockout
    table for admin / HOD / email-based logins.

    Returns a 3-tuple: (int, int, str|None)
    """
    is_fac = identifier and "@" not in identifier
    try:
        if is_fac:
            with db() as conn:
                row = conn.execute(
                    "SELECT failed_attempts, is_locked, lock_until "
                    "FROM faculty WHERE fac_id=?",
                    (identifier,)
                ).fetchone()
            if row:
                return (row["failed_attempts"] or 0,
                        row["is_locked"] or 0,
                        row["lock_until"])
            return (0, 0, None)
        else:
            with db() as conn:
                row = conn.execute(
                    "SELECT failed_attempts, is_locked, lock_until "
                    "FROM login_lockout WHERE identifier=?",
                    (identifier,)
                ).fetchone()
            if row:
                return (row["failed_attempts"] or 0,
                        row["is_locked"] or 0,
                        row["lock_until"])
            return (0, 0, None)
    except Exception as _e:
        log.error("get_login_fail_count(%s): %s", identifier, _e)
        return (0, 0, None)


def increment_login_fail(identifier: str) -> None:
    """Increment failed_attempts for identifier.

    If the new count >= _LOCKOUT_MAX_ATTEMPTS, sets is_locked=1 and
    lock_until = now + _LOCKOUT_DURATION_MIN minutes.

    Uses the faculty table for fac_id logins; login_lockout for all others.
    """
    is_fac = identifier and "@" not in identifier
    lock_time = (datetime.now() + timedelta(minutes=_LOCKOUT_DURATION_MIN)
                 ).strftime("%Y-%m-%d %H:%M:%S")
    try:
        if is_fac:
            with db() as conn:
                conn.execute(
                    "UPDATE faculty "
                    "SET failed_attempts = COALESCE(failed_attempts,0) + 1, "
                    "    is_locked = CASE WHEN COALESCE(failed_attempts,0)+1 >= ? "
                    "                    THEN 1 ELSE 0 END, "
                    "    lock_until = CASE WHEN COALESCE(failed_attempts,0)+1 >= ? "
                    "                     THEN ? ELSE lock_until END "
                    "WHERE fac_id=?",
                    (_LOCKOUT_MAX_ATTEMPTS, _LOCKOUT_MAX_ATTEMPTS, lock_time, identifier)
                )
        else:
            with db() as conn:
                # Upsert into login_lockout
                existing = conn.execute(
                    "SELECT failed_attempts FROM login_lockout WHERE identifier=?",
                    (identifier,)
                ).fetchone()
                if existing:
                    new_count = (existing["failed_attempts"] or 0) + 1
                    locked    = 1 if new_count >= _LOCKOUT_MAX_ATTEMPTS else 0
                    lu        = lock_time if locked else None
                    conn.execute(
                        "UPDATE login_lockout "
                        "SET failed_attempts=?, is_locked=?, lock_until=? "
                        "WHERE identifier=?",
                        (new_count, locked, lu, identifier)
                    )
                else:
                    new_count = 1
                    locked    = 1 if new_count >= _LOCKOUT_MAX_ATTEMPTS else 0
                    lu        = lock_time if locked else None
                    conn.execute(
                        "INSERT INTO login_lockout "
                        "(identifier, failed_attempts, is_locked, lock_until) "
                        "VALUES (?,?,?,?)",
                        (identifier, new_count, locked, lu)
                    )
    except Exception as _e:
        log.error("increment_login_fail(%s): %s", identifier, _e)


def reset_login_fail(identifier: str) -> None:
    """Clear failed_attempts, is_locked, and lock_until on successful login.

    Uses the faculty table for fac_id logins; login_lockout for all others.
    Safe to call even if the row doesn't exist yet.
    """
    is_fac = identifier and "@" not in identifier
    try:
        if is_fac:
            with db() as conn:
                conn.execute(
                    "UPDATE faculty "
                    "SET failed_attempts=0, is_locked=0, lock_until=NULL "
                    "WHERE fac_id=?",
                    (identifier,)
                )
        else:
            with db() as conn:
                conn.execute(
                    "UPDATE login_lockout "
                    "SET failed_attempts=0, is_locked=0, lock_until=NULL "
                    "WHERE identifier=?",
                    (identifier,)
                )
    except Exception as _e:
        log.error("reset_login_fail(%s): %s", identifier, _e)


# =============================================================
# F-11: TOKEN BLOCKLIST HELPERS  (logout / revocation)
# =============================================================

def blocklist_token(jti: str, expires_at: str) -> None:
    """Insert a jti into the token_blocklist table.

    Uses INSERT OR IGNORE so a duplicate logout call is silently safe.

    Args:
        jti:        The unique JWT ID claim from the token payload.
        expires_at: ISO-8601 UTC string of the token's 'exp' — used by
                    cleanup_expired_tokens() to prune the table.
    """
    try:
        with db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO token_blocklist (jti, expires_at) "
                "VALUES (?, ?)",
                (jti, expires_at)
            )
    except Exception as _e:
        log.error("blocklist_token(%s): %s", jti, _e)


def is_token_blocked(jti: str) -> bool:
    """Return True if jti is present in the blocklist AND has not yet expired.

    An expired blocklist row (token TTL already past) is treated as not-blocked
    because the JWT library would have already rejected the token on signature
    / expiry grounds — there is nothing to revoke.
    """
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT 1 FROM token_blocklist "
                "WHERE jti=? AND expires_at > datetime('now','utc')",
                (jti,)
            ).fetchone()
        return row is not None
    except Exception as _e:
        log.error("is_token_blocked(%s): %s", jti, _e)
        return False


def cleanup_expired_tokens() -> int:
    """Delete all token_blocklist rows whose expires_at is in the past.

    Call this on startup (and optionally on a schedule) to prevent the
    table from accumulating stale rows indefinitely.

    Returns the number of rows deleted.
    """
    try:
        with db() as conn:
            cur = conn.execute(
                "DELETE FROM token_blocklist "
                "WHERE expires_at < datetime('now','utc')"
            )
        deleted = cur.rowcount
        if deleted:
            log.info("cleanup_expired_tokens: removed %d stale row(s).", deleted)
        return deleted
    except Exception as _e:
        log.error("cleanup_expired_tokens: %s", _e)
        return 0


# =============================================================
# F-13: TOKEN VERSION HELPERS  (session invalidation on pw change)
# =============================================================

def get_token_version(identifier: str) -> int:
    """Return the current token version for the given identifier (default 0).

    Args:
        identifier: The 'sub' claim from the JWT — typically the user's email.

    Returns the version integer.  If no row exists yet, 0 is returned so that
    tokens minted before the first password change are always valid (version 0
    in both the token and the (non-existent) DB row).
    """
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT version FROM user_token_version WHERE identifier=?",
                (identifier,)
            ).fetchone()
        return int(row["version"]) if row else 0
    except Exception as _e:
        log.error("get_token_version(%s): %s", identifier, _e)
        return 0


def increment_token_version(identifier: str) -> None:
    """Increment (or initialise) the token version for identifier.

    Uses INSERT OR REPLACE so a missing row is created with version=1 and an
    existing row is atomically incremented.  After this call, all previously
    issued tokens for this user carry an older 'tv' value and are rejected
    by decode_token().

    Args:
        identifier: The 'sub' claim from the JWT — typically the user's email.
    """
    try:
        with db() as conn:
            conn.execute(
                """
                INSERT INTO user_token_version (identifier, version, updated_at)
                VALUES (?, 1, datetime('now','utc'))
                ON CONFLICT(identifier) DO UPDATE SET
                    version    = version + 1,
                    updated_at = datetime('now','utc')
                """,
                (identifier,)
            )
    except Exception as _e:
        log.error("increment_token_version(%s): %s", identifier, _e)


# =============================================================
# F-14: PASSWORD HISTORY HELPERS  (reuse prevention)
# =============================================================
_PASSWORD_HISTORY_LIMIT = 5   # how many previous hashes to retain per user


def add_password_history(identifier: str, pwd_hash: str) -> None:
    """Record a newly set bcrypt hash for *identifier*.

    After inserting the new row the function prunes any rows beyond the
    most-recent _PASSWORD_HISTORY_LIMIT entries so the table stays bounded.

    Args:
        identifier: The user's email / 'sub' claim — same key used across
                    reset_password() and change_password().
        pwd_hash:   The bcrypt hash of the new password (starts with '$2b$').
                    Plain-text passwords must NEVER be passed here.
    """
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO password_history (identifier, pwd_hash) VALUES (?, ?)",
                (identifier, pwd_hash)
            )
            # Prune: keep only the _PASSWORD_HISTORY_LIMIT most-recent rows.
            # Rows older than the Nth newest are deleted in one statement.
            conn.execute(
                """
                DELETE FROM password_history
                WHERE identifier = ?
                  AND id NOT IN (
                      SELECT id FROM password_history
                      WHERE  identifier = ?
                      ORDER  BY created_at DESC
                      LIMIT  ?
                  )
                """,
                (identifier, identifier, _PASSWORD_HISTORY_LIMIT)
            )
    except Exception as _e:
        log.error("add_password_history(%s): %s", identifier, _e)


def is_password_reused(identifier: str, new_plain: str) -> bool:
    """Return True if *new_plain* matches any of the last 5 stored hashes.

    Uses verify_password() (bcrypt) for comparison — the plain-text
    password is never stored and is not written to the log on failure.

    Args:
        identifier: The user's email / 'sub' claim.
        new_plain:  The candidate plain-text password supplied by the user.

    Returns True (reused) or False (not reused / history empty).
    Never raises — a DB or bcrypt error is treated as not-reused so that
    a transient failure never blocks a legitimate password change.
    """
    try:
        from auth_utils import verify_password as _vp
    except Exception as _imp:
        log.error("is_password_reused: cannot import verify_password — %s", _imp)
        return False

    try:
        with db() as conn:
            rows = conn.execute(
                """
                SELECT pwd_hash FROM password_history
                WHERE  identifier = ?
                ORDER  BY created_at DESC
                LIMIT  ?
                """,
                (identifier, _PASSWORD_HISTORY_LIMIT)
            ).fetchall()
    except Exception as _e:
        log.error("is_password_reused(%s): DB error — %s", identifier, _e)
        return False

    for row in rows:
        try:
            if _vp(new_plain, row["pwd_hash"]):
                return True
        except Exception:
            # Malformed hash in history — skip gracefully
            continue
    return False


def add_student(student_id, name, roll_number, section="",
                mobile="", twin_of=None, consent=True,
                register_number="", first_name="", last_name="",
                gender="", date_of_birth="", department="",
                course="", year="", student_email="",
                parent_email="", student_mobile="", parent_mobile="",
                status="Active") -> bool:
    if mobile and not student_mobile:
        student_mobile = mobile
    try:
        with db() as conn:
            conn.execute("""
                INSERT INTO students
                    (student_id, name, register_number, roll_number,
                     first_name, last_name, gender, date_of_birth,
                     department, course, year, section,
                     student_email, parent_email, student_mobile, parent_mobile,
                     status, twin_of, consent, enrolled_on, active)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
            """, (student_id, name, register_number, roll_number.lower() if roll_number else "",
                  first_name, last_name, gender, date_of_birth,
                  department, course, year, section,
                  student_email, parent_email, student_mobile, parent_mobile,
                  status, twin_of, 1 if consent else 0,
                  datetime.now().strftime("%Y-%m-%d")))
        return True
    except sqlite3.IntegrityError:
        return False


def delete_student_data(student_id: str):
    """Permanently remove a student from the system.
    Deletes from: students table, student_attendance, student_timetable,
    student_extended.  This ensures the record disappears from all views
    (including the attendance-fallback path) after deletion.
    """
    with db() as conn:
        conn.execute("DELETE FROM students WHERE student_id=?", (student_id,))
        try:
            conn.execute("DELETE FROM student_attendance WHERE student_id=?", (student_id,))
        except Exception:
            pass
        try:
            conn.execute("DELETE FROM student_timetable WHERE student_id=?", (student_id,))
        except Exception:
            pass
        try:
            conn.execute("DELETE FROM student_extended WHERE student_id=?", (student_id,))
        except Exception:
            pass
        try:
            conn.execute("DELETE FROM attendance WHERE student_id=?", (student_id,))
        except Exception:
            pass


def register_twin_pair(id1: str, id2: str):
    """Register a twin pair — update twin_of for both students."""
    with db() as conn:
        conn.execute("UPDATE students SET twin_of=? WHERE student_id=?", (id2, id1))
        conn.execute("UPDATE students SET twin_of=? WHERE student_id=?", (id1, id2))
    log.info("Twin pair registered: %s <-> %s", id1, id2)


def get_all_twin_pairs():
    with db() as conn:
        rows = conn.execute("""
            SELECT s1.student_id AS id1, s1.name AS name1,
                   s2.student_id AS id2, s2.name AS name2
            FROM students s1
            JOIN students s2 ON s1.twin_of = s2.student_id
            WHERE s1.active=1 AND s2.active=1
              AND s1.student_id < s2.student_id
        """).fetchall()
        return [dict(r) for r in rows]


# =============================================================
# ATTENDANCE
# =============================================================
def mark_attendance(student_id, name, period, confidence,
                    engine, camera_id="CAM1",
                    liveness_score=0.0, twin_verified=False,
                    skeleton_score=0.0) -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    now   = datetime.now().strftime("%H:%M:%S")
    try:
        with db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO attendance
                    (student_id,name,period,date,time,confidence,engine,
                     camera_id,liveness_score,twin_verified,skeleton_score)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (student_id, name, period, today, now, confidence,
                  engine, camera_id, liveness_score,
                  1 if twin_verified else 0, skeleton_score))
        return True
    except Exception as e:
        log.error("mark_attendance error: %s", e)
        return False


def is_already_marked(student_id: str, period: str) -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM attendance WHERE student_id=? AND period=? AND date=?",
            (student_id, period, today)
        ).fetchone()
    return row is not None


def get_today_attendance(period: str = None):
    today = datetime.now().strftime("%Y-%m-%d")
    return get_attendance_by_date(today, period)


def get_attendance_by_date(date_str: str, period: str = None):
    with db() as conn:
        # ── Try role-based student_attendance table first ─────────────────
        try:
            if period:
                role_rows = conn.execute("""
                    SELECT student_id, name, department,
                           date, time, period, status, confidence,
                           '' AS roll_number,
                           'student_attendance' AS engine
                    FROM student_attendance
                    WHERE date=? AND period=?
                    ORDER BY time
                """, (date_str, period)).fetchall()
            else:
                role_rows = conn.execute("""
                    SELECT student_id, name, department,
                           date, time, period, status, confidence,
                           '' AS roll_number,
                           'student_attendance' AS engine
                    FROM student_attendance
                    WHERE date=?
                    ORDER BY period, time
                """, (date_str,)).fetchall()
            if role_rows:
                return [dict(r) for r in role_rows]
        except Exception:
            pass  # student_attendance table may not exist — fall through

        # ── Fallback: legacy attendance table ─────────────────────────────
        if period:
            rows = conn.execute("""
                SELECT a.*, s.roll_number
                FROM attendance a
                LEFT JOIN students s ON a.student_id = s.student_id
                WHERE a.date=? AND a.period=?
                ORDER BY a.time
            """, (date_str, period)).fetchall()
        else:
            rows = conn.execute("""
                SELECT a.*, s.roll_number
                FROM attendance a
                LEFT JOIN students s ON a.student_id = s.student_id
                WHERE a.date=?
                ORDER BY a.period, a.time
            """, (date_str,)).fetchall()
        return [dict(r) for r in rows]


def teacher_override(student_id: str, period: str,
                     action: str, note: str = "",
                     teacher: str = "teacher"):
    today = datetime.now().strftime("%Y-%m-%d")
    now   = datetime.now().strftime("%H:%M:%S")
    with db() as conn:
        if action == "mark_present":
            row = conn.execute(
                "SELECT name FROM students WHERE student_id=?", (student_id,)
            ).fetchone()
            name = row["name"] if row else student_id
            conn.execute("""
                INSERT OR REPLACE INTO attendance
                    (student_id,name,period,date,time,confidence,engine,camera_id)
                VALUES (?,?,?,?,?,1.0,'manual_override','TEACHER')
            """, (student_id, name, period, today, now))
        else:
            conn.execute(
                "DELETE FROM attendance WHERE student_id=? AND period=? AND date=?",
                (student_id, period, today)
            )
        conn.execute("""
            INSERT INTO override_log(student_id,period,action,note,teacher)
            VALUES (?,?,?,?,?)
        """, (student_id, period, action, note, teacher))


def get_override_log(limit: int = 200) -> list:
    """Return override log joined with student info, newest first."""
    with db() as conn:
        rows = conn.execute("""
            SELECT ol.id, ol.student_id, ol.period, ol.action, ol.note,
                   ol.teacher,
                   COALESCE(ol.created_at, datetime('now','localtime')) AS created_at,
                   s.name  AS student_name,
                   s.roll_number,
                   s.section,
                   s.department,
                   COALESCE(s.student_email, '') AS student_email,
                   COALESCE(s.parent_email,  '') AS parent_email
            FROM override_log ol
            LEFT JOIN students s ON s.student_id = ol.student_id
            ORDER BY ol.id DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_low_attendance_students(threshold: float = 75.0, days: int = 180) -> list:
    """
    Return students with attendance percentage below threshold, with emails.

    Default days=180 (one full semester).

    Fixes applied:
    - Removed `WHERE active = 1` filter — students may be enrolled with
      active=0 during database migration; filter on presence in attendance
      tables instead.
    - Denominator = actual school days (COUNT DISTINCT dates that appear
      in the attendance tables) so that a 180-day semester window over
      100 teaching days gives pct = present/100*100, not present/180*100.
    - Tries role-based student_attendance table first (with status filter),
      falls back to legacy attendance table.
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with db() as conn:
        # Actual school days in window
        actual_days: int = days
        try:
            r = conn.execute(
                "SELECT COUNT(DISTINCT date) FROM student_attendance "
                "WHERE date >= ? AND UPPER(status) = 'PRESENT'", (cutoff,)
            ).fetchone()
            if r and r[0]: actual_days = max(1, r[0])
        except Exception:
            pass
        if actual_days == days:
            try:
                r = conn.execute(
                    "SELECT COUNT(DISTINCT date) FROM attendance WHERE date >= ?",
                    (cutoff,)
                ).fetchone()
                if r and r[0]: actual_days = max(1, r[0])
            except Exception:
                actual_days = max(1, days)

        # ── Try role-based student_attendance table first ──────────────────
        try:
            role_rows = conn.execute("""
                SELECT s.student_id, s.name,
                       COALESCE(s.roll_number,   '')  AS roll_number,
                       COALESCE(s.section,       '')  AS section,
                       COALESCE(s.department,    '')  AS department,
                       COALESCE(s.student_email, '')  AS student_email,
                       COALESCE(s.parent_email,  '')  AS parent_email,
                       COUNT(DISTINCT sa.date)        AS present_count,
                       ?                              AS total_days,
                       ROUND(COUNT(DISTINCT sa.date) * 100.0 / ?, 1) AS pct
                FROM students s
                LEFT JOIN student_attendance sa
                    ON s.student_id = sa.student_id
                   AND sa.date >= ?
                   AND UPPER(sa.status) = 'PRESENT'
                GROUP BY s.student_id
                HAVING pct < ?
                ORDER BY pct ASC
            """, (actual_days, actual_days, cutoff, threshold)).fetchall()
            if role_rows:
                return [dict(r) for r in role_rows]
        except Exception:
            pass  # table may not exist — fall through

        # ── Fallback: legacy attendance table (no active=1 filter) ────────
        rows = conn.execute("""
            SELECT s.student_id, s.name,
                   COALESCE(s.roll_number,   '')  AS roll_number,
                   COALESCE(s.section,       '')  AS section,
                   COALESCE(s.department,    '')  AS department,
                   COALESCE(s.student_email, '')  AS student_email,
                   COALESCE(s.parent_email,  '')  AS parent_email,
                   COUNT(DISTINCT a.date)         AS present_count,
                   ?                              AS total_days,
                   ROUND(COUNT(DISTINCT a.date) * 100.0 / ?, 1) AS pct
            FROM students s
            LEFT JOIN attendance a
                ON s.student_id = a.student_id AND a.date >= ?
            GROUP BY s.student_id
            HAVING pct < ?
            ORDER BY pct ASC
        """, (actual_days, actual_days, cutoff, threshold)).fetchall()
        return [dict(r) for r in rows]


# =============================================================
# DASHBOARD STATS  — FIX: replaces the buggy len(today_rows) approach
# =============================================================
def get_dashboard_stats() -> dict:
    """
    Return accurate INSTITUTION-WIDE dashboard counts for today.

    Counts ALL active users in the institution:
      - Students  (students table, active=1)
      - Faculty   (faculty table,  active=1)
      - HODs      (hods table,     active=1)

    Uses COUNT(DISTINCT student_id) so that a person attending
    multiple periods is counted only once, not once per period row.

    Query priority mirrors get_attendance_by_date():
      1. student_attendance  (role-based sessions)
      2. attendance          (legacy face-recognition sessions)

    Returns:
        {
            "total_members":  int,   # all active institution members
            "total_students": int,   # kept for backward-compat (students only)
            "present_today":  int,   # unique members present today
            "absent_today":   int,   # total_members - present_today
            "pct_today":      float  # attendance percentage (1 d.p.)
        }
    """
    today = datetime.now().strftime("%Y-%m-%d")

    with db() as conn:
        # ── 1. Total active INSTITUTION members ───────────────────────────
        # Students
        student_count: int = 0
        try:
            student_count = conn.execute(
                "SELECT COUNT(*) FROM students WHERE active = 1"
            ).fetchone()[0] or 0
        except Exception:
            student_count = 0

        # Faculty
        faculty_count: int = 0
        try:
            faculty_count = conn.execute(
                "SELECT COUNT(*) FROM faculty WHERE active = 1"
            ).fetchone()[0] or 0
        except Exception:
            faculty_count = 0

        # HODs
        hod_count: int = 0
        try:
            hod_count = conn.execute(
                "SELECT COUNT(*) FROM hods WHERE active = 1"
            ).fetchone()[0] or 0
        except Exception:
            hod_count = 0

        total_members: int = student_count + faculty_count + hod_count
        # Backward-compat alias
        total_students: int = student_count

        # ── 2. Unique members present today ──────────────────────────────
        # Try the role-based student_attendance table first.
        present_today: int = 0
        try:
            row = conn.execute("""
                SELECT COUNT(DISTINCT student_id)
                FROM student_attendance
                WHERE date = ?
                  AND UPPER(status) = 'PRESENT'
            """, (today,)).fetchone()
            present_today = row[0] if row else 0
        except Exception:
            pass  # table may not exist yet — fall through to legacy table

        # If role-based table returned nothing, try the legacy attendance table.
        # The legacy table has no explicit status column; every row represents
        # a "present" record (absent members simply have no row).
        if present_today == 0:
            try:
                row = conn.execute("""
                    SELECT COUNT(DISTINCT student_id)
                    FROM attendance
                    WHERE date = ?
                """, (today,)).fetchone()
                present_today = row[0] if row else 0
            except Exception:
                present_today = 0

        # ── 3. Derived metrics (use institution total, not student-only) ──
        absent_today: int = max(0, total_members - present_today)

        if total_members > 0:
            pct_today: float = round(present_today / total_members * 100, 1)
        else:
            pct_today = 0.0

    return {
        "total_members":  total_members,
        "total_students": total_students,   # backward-compat (students only)
        "student_count":  student_count,
        "faculty_count":  faculty_count,
        "hod_count":      hod_count,
        "present_today":  present_today,
        "absent_today":   absent_today,
        "pct_today":      pct_today,
    }


# =============================================================
# ATTENDANCE SUMMARY  — FIX: status filter added
# =============================================================
def get_attendance_summary(days: int = 30):
    """
    Per-student attendance summary over the last `days` calendar days.

    Returns field names `present_count` and `total_days` (consistent with
    the rest of the codebase and with what the frontend JS expects).
    Also returns section, department, student_email, parent_email so that
    the reports page and alert fallback have everything they need.

    Uses actual school days (COUNT DISTINCT dates in window) as denominator
    so that pct is correct even when the window spans a holiday period.
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with db() as conn:
        # Actual school days in window — used as denominator so that a
        # 90-day window over 30 teaching days gives pct = present/30*100.
        actual_days: int = days  # fallback to calendar days
        try:
            r = conn.execute(
                "SELECT COUNT(DISTINCT date) FROM student_attendance "
                "WHERE date >= ? AND UPPER(status) = 'PRESENT'", (cutoff,)
            ).fetchone()
            if r and r[0]: actual_days = max(1, r[0])
        except Exception:
            pass
        if actual_days == days:
            try:
                r = conn.execute(
                    "SELECT COUNT(DISTINCT date) FROM attendance WHERE date >= ?",
                    (cutoff,)
                ).fetchone()
                if r and r[0]: actual_days = max(1, r[0])
            except Exception:
                pass

        # ── Try role-based student_attendance table first ─────────────────
        try:
            role_rows = conn.execute("""
                SELECT sa.student_id,
                       sa.name,
                       COALESCE(s.roll_number, '')   AS roll_number,
                       COALESCE(s.section,    '')    AS section,
                       COALESCE(sa.department, s.department, '') AS department,
                       COALESCE(s.student_email, '') AS student_email,
                       COALESCE(s.parent_email,  '') AS parent_email,
                       COUNT(DISTINCT sa.date)       AS present_count,
                       ?                             AS total_days
                FROM student_attendance sa
                LEFT JOIN students s ON s.student_id = sa.student_id
                WHERE sa.date >= ?
                  AND UPPER(sa.status) = 'PRESENT'
                GROUP BY sa.student_id
                ORDER BY sa.name
            """, (actual_days, cutoff)).fetchall()
            if role_rows:
                return [dict(r) for r in role_rows]
        except Exception:
            pass  # table may not exist — fall through

        # ── Fallback: legacy attendance + students tables ─────────────────
        rows = conn.execute("""
            SELECT s.student_id, s.name,
                   COALESCE(s.roll_number,    '')  AS roll_number,
                   COALESCE(s.section,        '')  AS section,
                   COALESCE(s.department,     '')  AS department,
                   COALESCE(s.student_email,  '')  AS student_email,
                   COALESCE(s.parent_email,   '')  AS parent_email,
                   COUNT(DISTINCT a.date)          AS present_count,
                   ?                               AS total_days
            FROM students s
            LEFT JOIN attendance a
                ON s.student_id = a.student_id AND a.date >= ?
            WHERE s.active = 1
            GROUP BY s.student_id
            ORDER BY s.name
        """, (actual_days, cutoff)).fetchall()
        return [dict(r) for r in rows]


def get_monthly_summary():
    year  = datetime.now().year
    month = datetime.now().month
    start = f"{year}-{month:02d}-01"
    with db() as conn:
        rows = conn.execute("""
            SELECT s.student_id, s.name, s.roll_number,
                   COUNT(DISTINCT a.date) AS days_present
            FROM students s
            LEFT JOIN attendance a
                ON s.student_id = a.student_id AND a.date >= ?
            WHERE s.active=1
            GROUP BY s.student_id
            ORDER BY s.name
        """, (start,)).fetchall()
        return [dict(r) for r in rows]


# =============================================================
# ANALYTICS
# =============================================================
def get_engine_stats(days: int = 7):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with db() as conn:
        rows = conn.execute("""
            SELECT engine, COUNT(*) AS count
            FROM attendance
            WHERE date >= ?
            GROUP BY engine
            ORDER BY count DESC
        """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]


def get_period_stats():
    today = datetime.now().strftime("%Y-%m-%d")
    with db() as conn:
        rows = conn.execute("""
            SELECT period, COUNT(*) AS count,
                   AVG(skeleton_score) AS avg_skeleton
            FROM attendance
            WHERE date = ?
            GROUP BY period
            ORDER BY period
        """, (today,)).fetchall()
        return [dict(r) for r in rows]


def get_twin_analysis_log(days: int = 7):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with db() as conn:
        rows = conn.execute("""
            SELECT * FROM twin_analysis_log
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT 100
        """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]


def log_twin_analysis(student_id, name="", twin_id="",
                      verified=False, confidence=0.0, method="svm",
                      period="", iris_score=0.0, skeleton_score=0.0,
                      periocular_score=0.0, geometry_score=0.0,
                      final_confidence=0.0, decision="",
                      feature_vector="",
                      twin_partner_id=None):
    """
    Log twin analysis result. Accepts both new and old call signatures.
    twin_partner_id is an alias for twin_id (backward compatibility).
    """
    if twin_partner_id is not None and not twin_id:
        twin_id = twin_partner_id
    if final_confidence == 0.0 and confidence > 0.0:
        final_confidence = confidence

    try:
        with db() as conn:
            conn.execute("""
                INSERT INTO twin_analysis_log
                    (student_id, name, twin_id, verified, confidence,
                     method, period, iris_score, skeleton_score,
                     periocular_score, geometry_score, final_confidence,
                     decision, feature_vector)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (student_id, name, twin_id,
                  1 if verified else 0, confidence,
                  method, period, iris_score, skeleton_score,
                  periocular_score, geometry_score, final_confidence,
                  decision, feature_vector))
    except Exception as e:
        log.warning("log_twin_analysis error (non-fatal): %s", e)


# =============================================================
# TIMETABLE
# =============================================================
def get_timetable():
    with db() as conn:
        rows = conn.execute("""
            SELECT period_name AS name, start_time AS start, end_time AS end
            FROM timetable WHERE active=1 ORDER BY start_time
        """).fetchall()
        result = [dict(r) for r in rows]
        return result if result else config.DEFAULT_PERIODS


def get_current_period() -> str:
    periods = get_timetable()
    now = datetime.now().strftime("%H:%M")
    for p in periods:
        s = str(p.get("start", ""))[:5]
        e = str(p.get("end", ""))[:5]
        if s <= now <= e:
            return p.get("name", "")
    return None


# =============================================================
# AUDIT LOG
# =============================================================
def log_audit(user_name, action, resource="", detail="", ip_address=""):
    try:
        with db() as conn:
            conn.execute("""
                INSERT INTO audit_log(user_name,action,resource,detail,ip_address)
                VALUES (?,?,?,?,?)
            """, (user_name, action, resource, detail, ip_address))
    except Exception as e:
        log.warning("audit_log failed (non-fatal): %s", e)


# =============================================================
# STAFF ENROLLMENT
# =============================================================
def add_staff(staff_id, employee_code, first_name, last_name, gender,
              date_of_birth, department, designation, email, mobile,
              joining_date) -> bool:
    """Insert staff record into faculty (canonical table)."""
    from datetime import datetime
    full_name = f"{first_name} {last_name}".strip()
    enrolled_today = datetime.now().strftime("%Y-%m-%d")
    try:
        with db() as conn:
            conn.execute("""
                INSERT INTO faculty
                    (fac_id,  name,  first_name,  last_name,  gender,
                     dept,    designation,  email,  mobile,
                     date_of_birth,  dob,
                     employee_code,  joining_date,
                     role,    status,  enrolled_on,  active)
                VALUES (?,?,?,?,?,
                        ?,?,?,?,
                        ?,?,
                        ?,?,
                        'Faculty','Active',?,1)
            """, (staff_id,   full_name, first_name, last_name, gender,
                  department, designation, email, mobile,
                  date_of_birth, date_of_birth,
                  employee_code, joining_date,
                  enrolled_today))
        return True
    except Exception:
        return False


def get_staff_by_id(staff_id: str):
    """Return staff record as dict, or None if not found."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM faculty WHERE fac_id=? AND active=1", (staff_id,)
        ).fetchone()
        if not row:
            return None
        record = dict(row)
        record.setdefault("staff_id",      record.get("fac_id"))
        record.setdefault("department",    record.get("dept"))
        record.setdefault("date_of_birth", record.get("dob") or record.get("date_of_birth"))
        return record


# =============================================================
# HOD ENROLLMENT
# =============================================================
def add_hod(hod_id, employee_code, first_name, last_name, gender,
            date_of_birth, department, email, mobile, joining_date) -> bool:
    """Insert or update HOD record in the canonical 'hods' table.

    FIX v11.1: Changed from plain INSERT (which silently failed on duplicate
    hod_id) to an INSERT … ON CONFLICT upsert.  This ensures that:
      - A brand-new HOD is inserted with active=1.
      - A re-enrollment of an existing HOD (e.g. after face re-capture) updates
        the record AND forces active=1, recovering any HOD that was stuck at
        active=0 due to legacy data or a previous soft-delete.
    The password is NOT overwritten on re-enrollment so admin-set passwords
    are preserved.

    Also normalises the dept value to uppercase canonical form so it matches
    the frontend department dropdown (e.g. 'CS' → 'CSE', 'cse' → 'CSE').
    """
    from datetime import datetime

    # Normalise department key to match frontend dropdown values
    _DEPT_ALIASES = {
        "cs":                          "CSE",
        "computer science":            "CSE",
        "computer science and engineering": "CSE",
        "cse":                         "CSE",
        "electronics":                 "ECE",
        "electronics and communication": "ECE",
        "ece":                         "ECE",
        "information technology":      "IT",
        "it":                          "IT",
        "mechanical":                  "MECH",
        "mechanical engineering":      "MECH",
        "mech":                        "MECH",
        "civil":                       "CIVIL",
        "civil engineering":           "CIVIL",
        "electrical":                  "EEE",
        "eee":                         "EEE",
        "mba":                         "MBA",
        "mca":                         "MCA",
    }
    dept_norm = _DEPT_ALIASES.get(
        (department or "").strip().lower(),
        (department or "").strip().upper()
    )

    full_name = f"{first_name} {last_name}".strip() or first_name
    enrolled_today = datetime.now().strftime("%Y-%m-%d")
    try:
        with db() as conn:
            conn.execute("""
                INSERT INTO hods
                    (hod_id, name, dept, designation, email, mobile,
                     password, joined_on, active,
                     employee_code, first_name, last_name, gender,
                     date_of_birth, joining_date, status, enrolled_on, role)
                VALUES (?,?,?,'Head of Department',?,?,'Hod@123',?,1,
                        ?,?,?,?,?,?,'Active',?,'HOD')
                ON CONFLICT(hod_id) DO UPDATE SET
                    name        = excluded.name,
                    dept        = excluded.dept,
                    email       = CASE WHEN excluded.email != ''
                                       THEN excluded.email
                                       ELSE hods.email END,
                    mobile      = CASE WHEN excluded.mobile != ''
                                       THEN excluded.mobile
                                       ELSE hods.mobile END,
                    joined_on   = CASE WHEN excluded.joined_on != '' AND excluded.joined_on IS NOT NULL
                                       THEN excluded.joined_on
                                       ELSE hods.joined_on END,
                    active      = 1,
                    employee_code = excluded.employee_code,
                    first_name  = excluded.first_name,
                    last_name   = excluded.last_name,
                    gender      = excluded.gender,
                    date_of_birth = excluded.date_of_birth,
                    joining_date  = excluded.joining_date,
                    status      = 'Active',
                    enrolled_on = excluded.enrolled_on,
                    role        = 'HOD'
            """, (hod_id, full_name, dept_norm, email, mobile,
                  joining_date,
                  employee_code, first_name, last_name, gender,
                  date_of_birth, joining_date,
                  enrolled_today))
        return True
    except Exception:
        return False


def get_hod_by_id(hod_id: str):
    """Return HOD dict or None. Reads from canonical 'hods' table."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM hods WHERE hod_id=? AND active=1", (hod_id,)
        ).fetchone()
        return dict(row) if row else None


# =============================================================
# Fetch helpers for train_selective.py
# =============================================================

def get_all_faculty_ids() -> list:
    """Return list of all active fac_id values from the faculty table."""
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT fac_id FROM faculty WHERE active=1 ORDER BY fac_id"
            ).fetchall()
            return [r["fac_id"] for r in rows]
    except Exception:
        return []


def get_all_hod_ids() -> list:
    """Return list of all active hod_id values from the canonical 'hods' table."""
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT hod_id FROM hods WHERE active=1 ORDER BY hod_id"
            ).fetchall()
            return [r["hod_id"] for r in rows]
    except Exception:
        return []


def get_all_student_ids() -> list:
    """Return list of all active student_id values."""
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT student_id FROM students WHERE active=1 ORDER BY student_id"
            ).fetchall()
            return [r["student_id"] for r in rows]
    except Exception:
        return []


# =============================================================
# MOBILE DUPLICATE CHECK
# =============================================================
def check_email_duplicate(email: str, exclude_id: str = "") -> dict:
    """Check if an email address already exists across students, faculty, and hods.

    Returns:
        {"exists": False} if the email is free.
        {"exists": True, "role": "student"|"faculty"|"hod", "name": <name>}
        if it is already registered.
    """
    if not email:
        return {"exists": False}
    email_lower = email.strip().lower()
    try:
        with db() as conn:
            # Check students (student_email and parent_email)
            row = conn.execute(
                """SELECT name, student_id AS entity_id FROM students
                   WHERE active=1
                     AND (LOWER(student_email)=? OR LOWER(parent_email)=?)
                     AND student_id != ?""",
                (email_lower, email_lower, exclude_id or "")
            ).fetchone()
            if row:
                return {"exists": True, "role": "student", "name": row["name"]}

            # Check faculty
            row = conn.execute(
                """SELECT name, fac_id AS entity_id FROM faculty
                   WHERE active=1 AND LOWER(email)=? AND fac_id != ?""",
                (email_lower, exclude_id or "")
            ).fetchone()
            if row:
                return {"exists": True, "role": "faculty", "name": row["name"]}

            # Check hods
            row = conn.execute(
                """SELECT name, hod_id AS entity_id FROM hods
                   WHERE active=1 AND LOWER(email)=? AND hod_id != ?""",
                (email_lower, exclude_id or "")
            ).fetchone()
            if row:
                return {"exists": True, "role": "hod", "name": row["name"]}

        return {"exists": False}
    except Exception:
        return {"exists": False}


def check_mobile_duplicate(mobile: str, exclude_id: str = "") -> dict:
    """Check if a mobile number already exists across students, faculty, and hods.

    Returns:
        {"exists": False} if the number is free.
        {"exists": True, "role": "student"|"faculty"|"hod", "name": <name>}
        if it is already registered.
    """
    if not mobile:
        return {"exists": False}
    try:
        with db() as conn:
            # Check students (student_mobile and parent_mobile)
            row = conn.execute(
                """SELECT name, student_id AS entity_id FROM students
                   WHERE active=1
                     AND (student_mobile=? OR parent_mobile=?)
                     AND student_id != ?""",
                (mobile, mobile, exclude_id or "")
            ).fetchone()
            if row:
                return {"exists": True, "role": "student", "name": row["name"]}

            # Check faculty
            row = conn.execute(
                """SELECT name, fac_id AS entity_id FROM faculty
                   WHERE active=1 AND mobile=? AND fac_id != ?""",
                (mobile, exclude_id or "")
            ).fetchone()
            if row:
                return {"exists": True, "role": "faculty", "name": row["name"]}

            # Check hods
            row = conn.execute(
                """SELECT name, hod_id AS entity_id FROM hods
                   WHERE active=1 AND mobile=? AND hod_id != ?""",
                (mobile, exclude_id or "")
            ).fetchone()
            if row:
                return {"exists": True, "role": "hod", "name": row["name"]}

        return {"exists": False}
    except Exception:
        return {"exists": False}


# =============================================================
# STARTUP
# =============================================================
try:
    init_db()
except Exception as _e:
    log.error("DB init failed: %s", _e)