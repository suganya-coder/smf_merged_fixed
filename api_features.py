
# =============================================================
# api_features.py  —  EduTrack Pro Feature Extensions
#
# FEATURE 1: Department Drill-Down Analytics
#   GET  /api/departments            → list departments with live stats
#   GET  /api/departments/{dept}/courses → courses + stats for dept
#   GET  /api/departments/{dept}/courses/{course}/sections → sections
#   GET  /api/departments/{dept}/courses/{course}/sections/{sec}/students
#            → student list with per-student attendance %
#
# FEATURE 2: Faculty Management
#   GET  /api/faculty                → list all faculty
#   POST /api/faculty                → create faculty record
#   GET  /api/faculty/{fac_id}       → single faculty profile + history
#   GET  /api/faculty/analytics/summary → KPI strip + chart data
#   POST /api/faculty/attendance     → mark faculty attendance
#   PUT  /api/faculty/{fac_id}/attendance/{log_id} → edit log entry
#   DELETE /api/faculty/{fac_id}/attendance/{log_id}
#   GET  /api/faculty/export/csv     → CSV download
#
# All new routes are mounted in create_app() via register_feature_routes()
# and require the same JWT token used by the rest of the system.
# =============================================================

import os
import re
import csv
import io
import sqlite3
import logging
from datetime import datetime, timedelta, date

log = logging.getLogger(__name__)

# ── Dept / Course / Section taxonomy ────────────────────────
# This mirrors what the frontend DEPTS / COURSE_META constants define.
# Departments are derived from the roll_number prefix stored on students
# (e.g. "23CS086" → dept key "CS").  For a fresh install the table has
# no students; the helpers below fall back to a sensible default list so
# the UI always has something to show.
#
# Each student row is expected to carry:
#   section   → "A" | "B" | …
#   roll_number → used to derive dept / course (e.g. "23CS086")
#
# For the Faculty Management feature, faculty records live in the
# `faculty` table (created below if absent) and attendance logs in
# `faculty_attendance`.

# =============================================================
# DB HELPERS
# =============================================================

def _db_path():
    import config
    return os.path.join(config.BASE_DIR, "attendance.db")


def _conn():
    """Open a WAL-mode SQLite connection with Row factory."""
    c = sqlite3.connect(_db_path(), timeout=15, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


# ── Name validation helper ────────────────────────────────────
_NAME_RE_FEAT = re.compile(r'^[A-Za-z]+( [A-Za-z]+)*$')

def _validate_name_feat(value: str, field: str) -> str:
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
    if not _NAME_RE_FEAT.match(v):
        raise HTTPException(status_code=422, detail=f"{field} must contain only letters and single spaces.")
    return v



# ── Mobile validation helper ──────────────────────────────────
_MOBILE_RE_FEAT = re.compile(r'^[6-9][0-9]{9}$')

def _validate_mobile_feat(value: str, field: str = "Mobile Number",
                           exclude_id: str = "") -> str:
    """Validate Indian mobile number for faculty/staff. Raises HTTPException on failure."""
    from fastapi import HTTPException
    import database as _db
    v = (value or "").strip()
    if not v:
        return v  # mobile is optional for faculty
    if re.search(r'[A-Za-z]', v):
        raise HTTPException(status_code=422,
                            detail=f"{field} must not contain letters.")
    if re.search(r'[^0-9]', v):
        raise HTTPException(status_code=422,
                            detail=f"{field} must not contain special characters.")
    if len(v) != 10:
        raise HTTPException(status_code=422,
                            detail=f"{field} must be exactly 10 digits.")
    if not _MOBILE_RE_FEAT.match(v):
        raise HTTPException(status_code=422,
                            detail=f"{field} must start with 6, 7, 8, or 9.")
    if exclude_id:
        try:
            dup = _db.check_mobile_duplicate(v, exclude_id)
            if dup.get("exists"):
                role = dup.get("role", "record")
                name = dup.get("name", "")
                raise HTTPException(status_code=409,
                                    detail=f"{field} {v} is already registered "
                                           f"to {name} ({role}).")
        except HTTPException:
            raise
        except Exception:
            pass  # duplicate check is best-effort
    return v


def _validate_dob_feat(dob_str: str, role: str, designation: str = "") -> str:
    """
    Validate date_of_birth for faculty/staff/HOD.
    Raises HTTPException(422) on any violation. Mirrors api._validate_dob().
    """
    from fastapi import HTTPException
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

    if role_norm in ("faculty", "staff"):
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


def _ensure_faculty_tables():
    """Create faculty & faculty_attendance tables if they don't exist."""
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS faculty (
            fac_id        TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            dept          TEXT NOT NULL DEFAULT '',
            designation   TEXT NOT NULL DEFAULT 'Assistant Professor',
            email         TEXT,
            mobile        TEXT,
            subjects      TEXT DEFAULT '[]',   -- JSON array stored as text
            joined_on     TEXT,
            active        INTEGER DEFAULT 1,
            dob           TEXT DEFAULT '',     -- Date of Birth (YYYY-MM-DD) used as login password
            class_incharge_dept    TEXT DEFAULT '',   -- dept of section they incharge
            class_incharge_year    INTEGER DEFAULT 0,
            class_incharge_section TEXT DEFAULT ''    -- e.g. 'B' for 3rd CSE-B
        );

        -- class_incharge lookup index
        CREATE INDEX IF NOT EXISTS idx_fac_incharge ON faculty(class_incharge_dept, class_incharge_year, class_incharge_section);

        CREATE TABLE IF NOT EXISTS faculty_attendance (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fac_id      TEXT NOT NULL,
            att_date    TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'present',
            arrival_time TEXT,
            reason      TEXT,
            updated_by  TEXT,
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(fac_id, att_date)
        );

        CREATE INDEX IF NOT EXISTS idx_fa_date  ON faculty_attendance(att_date);
        CREATE INDEX IF NOT EXISTS idx_fa_facid ON faculty_attendance(fac_id);
        """)
        # Safely add new columns to existing faculty tables
        for col, defn in [
            ("dob",                   "TEXT DEFAULT ''"),
            ("class_incharge_dept",    "TEXT DEFAULT ''"),
            ("class_incharge_year",    "INTEGER DEFAULT 0"),
            ("class_incharge_section", "TEXT DEFAULT ''"),
        ]:
            try:
                c.execute(f"ALTER TABLE faculty ADD COLUMN {col} {defn}")
            except Exception:
                pass
        c.commit()
        _seed_demo_faculty(c)


def _seed_demo_faculty(conn):
    """Insert demo rows only if the faculty table is empty."""
    count = conn.execute("SELECT COUNT(*) FROM faculty").fetchone()[0]
    if count > 0:
        return

    demo = [
        ("FAC001", "Dr. A. Kumar",       "CS",  "Professor",
         "kumar@college.edu",   "9900001111",
         '["Data Structures","Algorithms"]',     "1980-06-15"),
        ("FAC002", "Ms. R. Priya",       "CS",  "Assistant Professor",
         "priya@college.edu",   "9900002222",
         '["Web Technology","DBMS"]',            "1990-03-22"),
        ("FAC003", "Dr. S. Rajan",       "ECE", "Associate Professor",
         "rajan@college.edu",   "9900003333",
         '["DSP","Signals"]',                    "1978-11-05"),
        ("FAC004", "Mr. K. Venkat",      "ECE", "Assistant Professor",
         "venkat@college.edu",  "9900004444",
         '["VLSI","Embedded"]',                  "1985-08-19"),
        ("FAC005", "Dr. M. Lakshmi",     "MECH","Professor",
         "lakshmi@college.edu", "9900005555",
         '["Thermodynamics","Fluid Mechanics"]', "1975-01-30"),
        ("FAC006", "Ms. P. Deepa",       "MECH","Assistant Professor",
         "deepa@college.edu",   "9900006666",
         '["CAD","Manufacturing"]',              "1992-07-14"),
        ("FAC007", "Dr. T. Suresh",      "CIVIL","Associate Professor",
         "suresh@college.edu",  "9900007777",
         '["Structural Analysis","RCC Design"]', "1982-04-09"),
        ("FAC008", "Mr. G. Balamurugan","IT",  "Assistant Professor",
         "bala@college.edu",    "9900008888",
         '["Python Programming","AI"]',          "1988-12-25"),
    ]
    today = datetime.now().strftime("%Y-%m-%d")
    for row in demo:
        fac_id, name, dept, designation, email, mobile, subjects, dob = row
        try:
            conn.execute(
                "INSERT OR IGNORE INTO faculty"
                " (fac_id,name,dept,designation,email,mobile,subjects,joined_on,dob)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (fac_id, name, dept, designation, email, mobile, subjects, today, dob)
            )
        except Exception:
            pass

    # Seed ~30 days of random attendance per faculty
    import random
    random.seed(42)
    statuses = ["present"] * 18 + ["present"] * 5 + ["absent"] * 3 + \
               ["late"] * 2 + ["halfday"] * 1 + ["od"] * 1
    for fac_id, *_ in demo:
        for offset in range(30, 0, -1):
            d = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
            # Skip weekends
            wday = datetime.strptime(d, "%Y-%m-%d").weekday()
            if wday >= 5:
                continue
            st = random.choice(statuses)
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO faculty_attendance"
                    " (fac_id,att_date,status,arrival_time,updated_by)"
                    " VALUES (?,?,?,?,?)",
                    (fac_id, d, st,
                     "09:05" if st in ("present", "od") else
                     "09:45" if st == "late" else None,
                     "SYSTEM")
                )
            except Exception:
                pass
    conn.commit()


# =============================================================
# FEATURE 1: DEPARTMENT DRILL-DOWN  (pure SQL, no ORM)
# =============================================================

# ── Static taxonomy ──────────────────────────────────────────
# Kept in Python so the frontend can also use the raw data without
# needing a separate config endpoint.
DEPT_META = {
    "CSE":  {"name": "Computer Science & Engineering", "emoji": "💻", "color": "#4ecba8",
              "courses": {
                  "DS":   {"name": "Data Structures",
                            "years": {"I": ["A","B","C"], "II": ["A","B","C"], "III": ["A","B"], "IV": ["A","B"]}},
                  "AI":   {"name": "Artificial Intelligence",
                            "years": {"I": ["A","B"], "II": ["A","B"], "III": ["A","B"], "IV": ["A"]}},
                  "WEB":  {"name": "Web Technology",
                            "years": {"I": ["A","B"], "II": ["A","B"], "III": ["A"], "IV": ["A"]}},
                  "DBMS": {"name": "Database Systems",
                            "years": {"I": ["A","B"], "II": ["A","B"], "III": ["A","B"], "IV": ["A"]}}}},
    "AIDS": {"name": "AI & Data Science",              "emoji": "🤖", "color": "#b47cfd",
              "courses": {
                  "ML":   {"name": "Machine Learning",
                            "years": {"I": ["A","B"], "II": ["A","B"], "III": ["A"], "IV": ["A"]}},
                  "DL":   {"name": "Deep Learning",
                            "years": {"I": ["A"], "II": ["A"], "III": ["A"], "IV": ["A"]}},
                  "DA":   {"name": "Data Analytics",
                            "years": {"I": ["A","B"], "II": ["A","B"], "III": ["A"], "IV": ["A"]}}}},
    "IT":   {"name": "Information Technology",         "emoji": "🖧",  "color": "#ff7070",
              "courses": {
                  "PY":   {"name": "Python Programming",
                            "years": {"I": ["A","B"], "II": ["A","B"], "III": ["A"], "IV": ["A"]}},
                  "NET":  {"name": "Computer Networks",
                            "years": {"I": ["A"], "II": ["A"], "III": ["A"], "IV": ["A"]}},
                  "SE":   {"name": "Software Engineering",
                            "years": {"I": ["A","B"], "II": ["A","B"], "III": ["A"], "IV": ["A"]}}}},
    "CSBS": {"name": "CS & Business Systems",          "emoji": "📊", "color": "#f5a623",
              "courses": {
                  "BDA":  {"name": "Big Data Analytics",
                            "years": {"I": ["A"], "II": ["A"], "III": ["A"], "IV": ["A"]}},
                  "ERP":  {"name": "ERP Systems",
                            "years": {"I": ["A"], "II": ["A"], "III": ["A"], "IV": ["A"]}},
                  "BA":   {"name": "Business Analytics",
                            "years": {"I": ["A"], "II": ["A"], "III": ["A"], "IV": ["A"]}}}},
    "ECE":  {"name": "Electronics & Communication",    "emoji": "📡", "color": "#4da6f5",
              "courses": {
                  "DSP":  {"name": "Digital Signal Processing",
                            "years": {"I": ["A","B"], "II": ["A","B"], "III": ["A"], "IV": ["A"]}},
                  "VLSI": {"name": "VLSI Design",
                            "years": {"I": ["A","B"], "II": ["A","B"], "III": ["A"], "IV": ["A"]}},
                  "ES":   {"name": "Embedded Systems",
                            "years": {"I": ["A"], "II": ["A"], "III": ["A"], "IV": ["A"]}}}},
    "EEE":  {"name": "Electrical & Electronics",       "emoji": "⚡", "color": "#ffd700",
              "courses": {
                  "PE":   {"name": "Power Electronics",
                            "years": {"I": ["A","B"], "II": ["A","B"], "III": ["A"], "IV": ["A"]}},
                  "EM":   {"name": "Electrical Machines",
                            "years": {"I": ["A"], "II": ["A"], "III": ["A"], "IV": ["A"]}},
                  "PS":   {"name": "Power Systems",
                            "years": {"I": ["A"], "II": ["A"], "III": ["A"], "IV": ["A"]}}}},
    "BM":   {"name": "Bio Medical Engineering",        "emoji": "🏥", "color": "#ff6eb4",
              "courses": {
                  "BMI":  {"name": "Biomedical Instrumentation",
                            "years": {"I": ["A"], "II": ["A"], "III": ["A"], "IV": ["A"]}},
                  "BS":   {"name": "Biosensors",
                            "years": {"I": ["A"], "II": ["A"], "III": ["A"], "IV": ["A"]}},
                  "ME":   {"name": "Medical Electronics",
                            "years": {"I": ["A"], "II": ["A"], "III": ["A"], "IV": ["A"]}}}},
    "MECH": {"name": "Mechanical Engineering",         "emoji": "⚙️",  "color": "#ffb347",
              "courses": {
                  "TD":   {"name": "Thermodynamics",
                            "years": {"I": ["A","B"], "II": ["A","B"], "III": ["A"], "IV": ["A"]}},
                  "FM":   {"name": "Fluid Mechanics",
                            "years": {"I": ["A"], "II": ["A"], "III": ["A"], "IV": ["A"]}},
                  "CAD":  {"name": "CAD/CAM",
                            "years": {"I": ["A","B"], "II": ["A","B"], "III": ["A"], "IV": ["A"]}}}},
    "CIVIL":{"name": "Civil Engineering",              "emoji": "🏗️",  "color": "#9b87f5",
              "courses": {
                  "SA":   {"name": "Structural Analysis",
                            "years": {"I": ["A","B"], "II": ["A","B"], "III": ["A"], "IV": ["A"]}},
                  "RCC":  {"name": "RCC Design",
                            "years": {"I": ["A"], "II": ["A"], "III": ["A"], "IV": ["A"]}},
                  "SUR":  {"name": "Surveying",
                            "years": {"I": ["A"], "II": ["A"], "III": ["A"], "IV": ["A"]}}}},
}
def _student_att_pct(student_id: str, days: int = 30) -> float:
    """Return attendance % for a student over last `days` days."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(DISTINCT date) AS present FROM attendance"
            " WHERE student_id=? AND date>=?",
            (student_id, cutoff)
        ).fetchone()
        present = row["present"] if row else 0
    # count working days (Mon–Fri only)
    working = sum(
        1 for i in range(days)
        if (datetime.now() - timedelta(days=i)).weekday() < 5
    )
    return round(present / max(working, 1) * 100, 1)


def _section_stats(dept: str, course: str, section: str, days: int = 30):
    """Return aggregate stats for a dept/course/section."""
    # Derive students from the students table by roll_number pattern
    # roll_number format stored: "23cs086" → dept hint is "cs"
    # We use the section column directly + cross-reference dept via DEPT_META.
    # Because the demo system stores roll_numbers like "STU_XXXXX", we use
    # section + a deterministic assignment based on student index.
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _conn() as c:
        # All active students in this section
        students = c.execute(
            "SELECT student_id, name FROM students WHERE active=1 AND section=?",
            (section,)
        ).fetchall()

        if not students:
            return {"total": 0, "avg_att": 0, "good": 0, "warn": 0, "poor": 0}

        good = warn = poor = 0
        total_pct = 0.0
        working = max(
            sum(1 for i in range(days)
                if (datetime.now() - timedelta(days=i)).weekday() < 5), 1)

        for s in students:
            row = c.execute(
                "SELECT COUNT(DISTINCT date) AS p FROM attendance"
                " WHERE student_id=? AND date>=?",
                (s["student_id"], cutoff)
            ).fetchone()
            p = row["p"] if row else 0
            pct = round((p or 0) / max(working, 1) * 100, 1)
            total_pct += pct
            if pct >= 75:
                good += 1
            elif pct >= 65:
                warn += 1
            else:
                poor += 1

        return {
            "total": len(students),
            "avg_att": round(total_pct / len(students), 1),
            "good": good,
            "warn": warn,
            "poor": poor,
        }


def get_departments_overview() -> list:
    """
    Return list of dept objects each with live attendance stats.
    Used by the Institution Overview level.
    """
    result = []
    for dept_key, meta in DEPT_META.items():
        # Aggregate across all sections
        total_students = avg_att = good = warn = poor = 0
        for course_key, course_meta in meta["courses"].items():
            for sec in _flat_secs(course_meta):
                s = _section_stats(dept_key, course_key, sec)
                total_students += s["total"]
                avg_att += s["avg_att"] * s["total"]
                good += s["good"]; warn += s["warn"]; poor += s["poor"]

        avg_att = round(avg_att / max(total_students, 1), 1)
        result.append({
            "key":       dept_key,
            "name":      meta["name"],
            "emoji":     meta["emoji"],
            "color":     meta["color"],
            "course_count": len(meta["courses"]),
            "total_students": total_students,
            "avg_att":   avg_att,
            "good":      good,
            "warn":      warn,
            "poor":      poor,
        })
    return result


def _flat_secs(course_meta: dict) -> list:
    """Return deduplicated sorted list of all sections across all years."""
    if "secs" in course_meta:
        return course_meta["secs"]
    seen, result = set(), []
    for secs in course_meta.get("years", {}).values():
        for s in secs:
            if s not in seen:
                seen.add(s); result.append(s)
    return sorted(result)


def get_dept_courses(dept_key: str) -> dict:
    """Return course list + stats for a given dept."""
    meta = DEPT_META.get(dept_key)
    if not meta:
        return {}
    courses = []
    for ck, cm in meta["courses"].items():
        secs = _flat_secs(cm)
        stats = {"total": 0, "avg_att": 0, "good": 0, "warn": 0, "poor": 0}
        for sec in secs:
            s = _section_stats(dept_key, ck, sec)
            stats["total"] += s["total"]
            stats["avg_att"] += s["avg_att"] * s["total"]
            stats["good"] += s["good"]; stats["warn"] += s["warn"]
            stats["poor"] += s["poor"]
        stats["avg_att"] = round(stats["avg_att"] / max(stats["total"], 1), 1)
        courses.append({
            "key":     ck,
            "name":    cm["name"],
            "secs":    secs,
            "years":   list(cm.get("years", {}).keys()),
            **stats,
        })
    return {
        "dept_key":   dept_key,
        "dept_name":  meta["name"],
        "dept_color": meta["color"],
        "courses":    courses,
    }


def get_course_sections(dept_key: str, course_key: str,
                         year: str = None) -> dict:
    """Return sections list + stats for a dept/course (optionally filtered by year)."""
    meta = DEPT_META.get(dept_key)
    if not meta:
        return {}
    cm = meta["courses"].get(course_key)
    if not cm:
        return {}
    # Year-filtered sections
    if year and "years" in cm:
        secs = cm["years"].get(year.upper(), [])
    else:
        secs = _flat_secs(cm)
    sections = []
    for sec in secs:
        s = _section_stats(dept_key, course_key, sec)
        sections.append({"section": sec, **s})
    return {
        "dept_key":    dept_key,
        "course_key":  course_key,
        "course_name": cm["name"],
        "dept_color":  meta["color"],
        "year":        year,
        "sections":    sections,
    }


def get_course_years(dept_key: str, course_key: str) -> dict:
    """Return available years for a dept/course."""
    meta = DEPT_META.get(dept_key)
    if not meta:
        return {}
    cm = meta["courses"].get(course_key)
    if not cm:
        return {}
    years = list(cm.get("years", {}).keys())
    return {
        "dept_key":    dept_key,
        "course_key":  course_key,
        "course_name": cm["name"],
        "years":       years,
    }


def get_section_students(dept_key: str, course_key: str,
                          section: str, days: int = 30) -> dict:
    """Return student list with per-student attendance % for a section."""
    meta = DEPT_META.get(dept_key)
    cm   = (meta or {}).get("courses", {}).get(course_key, {})
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    working = max(
        sum(1 for i in range(days)
            if (datetime.now() - timedelta(days=i)).weekday() < 5), 1)

    with _conn() as c:
        students = c.execute(
            "SELECT student_id, name, roll_number, mobile, enrolled_on"
            " FROM students WHERE active=1 AND section=? ORDER BY name",
            (section,)
        ).fetchall()

        result = []
        for s in students:
            row = c.execute(
                "SELECT COUNT(DISTINCT date) AS p,"
                " MAX(date) AS last_date"
                " FROM attendance WHERE student_id=? AND date>=?",
                (s["student_id"], cutoff)
            ).fetchone()
            p    = row["p"] if row else 0
            last = (row["last_date"] or "")[:10] if row else ""
            pct  = round((p or 0) / max(working, 1) * 100, 1)
            result.append({
                "student_id":  s["student_id"],
                "name":        s["name"] or "?",
                "roll_number": s["roll_number"] or s["student_id"],
                "mobile":      s["mobile"] or "—",
                "enrolled_on": (s["enrolled_on"] or "")[:10],
                "present":     p,
                "total":       working,
                "att_pct":     pct,
                "last_seen":   last,
                "status":      ("good" if pct >= 75
                                else "warn" if pct >= 65 else "poor"),
            })

    return {
        "dept_key":    dept_key,
        "course_key":  course_key,
        "course_name": cm.get("name", course_key),
        "section":     section,
        "dept_color":  (meta or {}).get("color", "#4ecba8"),
        "students":    result,
        "stats": {
            "total":   len(result),
            "avg_att": round(sum(s["att_pct"] for s in result)
                            / max(len(result), 1), 1),
            "good":    sum(1 for s in result if s["status"] == "good"),
            "warn":    sum(1 for s in result if s["status"] == "warn"),
            "poor":    sum(1 for s in result if s["status"] == "poor"),
        },
    }


# =============================================================
# FEATURE 2: FACULTY MANAGEMENT
# =============================================================

def get_all_faculty(dept: str = None, search: str = None,
                    att_date: str = None) -> list:
    """Return faculty list enriched with today's status + 30-day att%."""
    _ensure_faculty_tables()
    target_date = att_date or datetime.now().strftime("%Y-%m-%d")
    cutoff30    = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    with _conn() as c:
        sql = "SELECT * FROM faculty WHERE active=1"
        params = []
        if dept:
            sql += " AND dept=?"; params.append(dept)
        if search:
            sql += " AND (name LIKE ? OR fac_id LIKE ?)"; params += [f"%{search}%"] * 2
        sql += " ORDER BY name"
        rows = c.execute(sql, params).fetchall()

        result = []
        for r in rows:
            fac = dict(r)

            # today's / selected date's status
            trow = c.execute(
                "SELECT status, arrival_time FROM faculty_attendance"
                " WHERE fac_id=? AND att_date=?",
                (fac["fac_id"], target_date)
            ).fetchone()
            fac["today_status"]   = trow["status"]        if trow else "not_marked"
            fac["today_arrival"]  = trow["arrival_time"]  if trow else None

            # 30-day attendance %
            arow = c.execute(
                "SELECT COUNT(*) AS total,"
                " SUM(CASE WHEN status IN ('present','late','halfday','od') THEN 1 ELSE 0 END) AS present"
                " FROM faculty_attendance"
                " WHERE fac_id=? AND att_date>=?",
                (fac["fac_id"], cutoff30)
            ).fetchone()
            total   = (arow["total"]   or 0) if arow else 0
            present = (arow["present"] or 0) if arow else 0
            working = max(
                sum(1 for i in range(30)
                    if (datetime.now() - timedelta(days=i)).weekday() < 5), 1)
            fac["att_pct"] = round((present or 0) / max(working, 1) * 100, 1)

            result.append(fac)
    return result


def get_faculty_detail(fac_id: str, days: int = 30) -> dict:
    """Return single faculty profile + full attendance history."""
    _ensure_faculty_tables()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _conn() as c:
        fac = c.execute(
            "SELECT * FROM faculty WHERE fac_id=? AND active=1", (fac_id,)
        ).fetchone()
        if not fac:
            return {}
        fac = dict(fac)

        logs = c.execute(
            "SELECT * FROM faculty_attendance"
            " WHERE fac_id=? AND att_date>=? ORDER BY att_date DESC",
            (fac_id, cutoff)
        ).fetchall()
        fac["attendance_log"] = [dict(l) for l in logs]

        # Stats
        total = len(logs)
        present = sum(1 for l in logs
                      if l["status"] in ("present", "late", "halfday", "od"))
        working = max(
            sum(1 for i in range(days)
                if (datetime.now() - timedelta(days=i)).weekday() < 5), 1)
        fac["att_pct"]     = round(present / working * 100, 1)
        fac["total_days"]  = total
        fac["present_days"]= present
        fac["absent_days"] = sum(1 for l in logs if l["status"] == "absent")

        # Monthly breakdown for sparkline (last 6 months)
        monthly = {}
        for l in logs:
            m = l["att_date"][:7]
            monthly.setdefault(m, {"present": 0, "total": 0})
            monthly[m]["total"] += 1
            if l["status"] in ("present", "late", "halfday", "od"):
                monthly[m]["present"] += 1
        fac["monthly"] = [
            {"month": m, **v,
             "pct": round((v["present"] or 0) / max(v["total"] or 1, 1) * 100, 1)}
            for m, v in sorted(monthly.items())
        ]

    return fac


def get_faculty_analytics() -> dict:
    """
    Aggregate KPIs + chart data for the faculty management page.
    Returns dept-wise bar chart data, status donut data, and KPI strip.
    """
    _ensure_faculty_tables()
    today   = datetime.now().strftime("%Y-%m-%d")
    cutoff  = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    working = max(
        sum(1 for i in range(30)
            if (datetime.now() - timedelta(days=i)).weekday() < 5), 1)

    with _conn() as c:
        # Overall counts
        total_fac = c.execute(
            "SELECT COUNT(*) FROM faculty WHERE active=1"
        ).fetchone()[0]

        today_rows = c.execute(
            "SELECT status, COUNT(*) AS cnt FROM faculty_attendance"
            " WHERE att_date=? GROUP BY status", (today,)
        ).fetchall()
        status_map = {r["status"]: r["cnt"] for r in today_rows}
        present_today = (status_map.get("present", 0) +
                         status_map.get("late", 0) +
                         status_map.get("halfday", 0) +
                         status_map.get("od", 0))
        absent_today  = status_map.get("absent", 0)
        not_marked    = total_fac - sum(status_map.values())

        # 30-day avg
        avg_row = c.execute(
            "SELECT f.fac_id,"
            " SUM(CASE WHEN a.status IN ('present','late','halfday','od') THEN 1 ELSE 0 END) AS p"
            " FROM faculty f"
            " LEFT JOIN faculty_attendance a"
            "   ON f.fac_id=a.fac_id AND a.att_date>=?"
            " WHERE f.active=1 GROUP BY f.fac_id",
            (cutoff,)
        ).fetchall()
        avg_pct = round(
            sum(r["p"] for r in avg_row) / max(len(avg_row) * working, 1) * 100, 1
        ) if avg_row else 0

        # Dept-wise attendance %
        dept_rows = c.execute(
            "SELECT f.dept,"
            " SUM(CASE WHEN a.status IN ('present','late','halfday','od') THEN 1 ELSE 0 END) AS p,"
            " COUNT(DISTINCT f.fac_id) AS fac_count"
            " FROM faculty f"
            " LEFT JOIN faculty_attendance a"
            "   ON f.fac_id=a.fac_id AND a.att_date>=?"
            " WHERE f.active=1 GROUP BY f.dept ORDER BY f.dept",
            (cutoff,)
        ).fetchall()
        dept_chart = [
            {
                "dept":      r["dept"],
                "fac_count": r["fac_count"],
                "att_pct":   round((r["p"] or 0) / max((r["fac_count"] or 1) * max(working,1), 1) * 100, 1),
            }
            for r in dept_rows
        ]

        # Status donut (today)
        donut = {
            "present": present_today,
            "absent":  absent_today,
            "not_marked": not_marked,
            "late":    status_map.get("late", 0),
            "od":      status_map.get("od", 0),
            "halfday": status_map.get("halfday", 0),
        }

        # Faculty comparison (top + bottom 5 by att%)
        fac_att = c.execute(
            "SELECT f.fac_id, f.name, f.dept,"
            " SUM(CASE WHEN a.status IN ('present','late','halfday','od') THEN 1 ELSE 0 END) AS p"
            " FROM faculty f"
            " LEFT JOIN faculty_attendance a"
            "   ON f.fac_id=a.fac_id AND a.att_date>=?"
            " WHERE f.active=1 GROUP BY f.fac_id ORDER BY p DESC",
            (cutoff,)
        ).fetchall()
        comparison = [
            {"fac_id": r["fac_id"], "name": r["name"], "dept": r["dept"],
             "att_pct": round((r["p"] or 0) / max(working, 1) * 100, 1)}
            for r in fac_att
        ]

    return {
        "total_faculty":   total_fac,
        "present_today":   present_today,
        "absent_today":    absent_today,
        "not_marked_today":not_marked,
        "avg_att_30d":     avg_pct,
        "dept_chart":      dept_chart,
        "status_donut":    donut,
        "comparison":      comparison,
    }


def mark_faculty_attendance(fac_id: str, att_date: str, status: str,
                             arrival_time: str = None, reason: str = "",
                             updated_by: str = "ADMIN") -> dict:
    """Insert or replace a faculty attendance log entry."""
    _ensure_faculty_tables()
    with _conn() as c:
        fac = c.execute(
            "SELECT fac_id FROM faculty WHERE fac_id=? AND active=1", (fac_id,)
        ).fetchone()
        if not fac:
            return {"ok": False, "error": "Faculty not found"}
        c.execute(
            "INSERT OR REPLACE INTO faculty_attendance"
            " (fac_id,att_date,status,arrival_time,reason,updated_by)"
            " VALUES (?,?,?,?,?,?)",
            (fac_id, att_date, status, arrival_time, reason, updated_by)
        )
        c.commit()
    return {"ok": True}


def edit_faculty_attendance(log_id: int, status: str,
                             arrival_time: str = None, reason: str = "",
                             updated_by: str = "ADMIN") -> dict:
    """Update a specific faculty_attendance row by its primary key."""
    _ensure_faculty_tables()
    with _conn() as c:
        c.execute(
            "UPDATE faculty_attendance"
            " SET status=?,arrival_time=?,reason=?,updated_by=?"
            " WHERE id=?",
            (status, arrival_time, reason, updated_by, log_id)
        )
        if c.execute("SELECT changes()").fetchone()[0] == 0:
            return {"ok": False, "error": "Log entry not found"}
        c.commit()
    return {"ok": True}


def delete_faculty_attendance(log_id: int) -> dict:
    """Delete a specific faculty_attendance row."""
    _ensure_faculty_tables()
    with _conn() as c:
        c.execute("DELETE FROM faculty_attendance WHERE id=?", (log_id,))
        if c.execute("SELECT changes()").fetchone()[0] == 0:
            return {"ok": False, "error": "Not found"}
        c.commit()
    return {"ok": True}


def create_faculty(fac_id: str, name: str, dept: str,
                   designation: str = "Assistant Professor",
                   email: str = "", mobile: str = "",
                   subjects: list = None,
                   class_incharge_dept: str = "",
                   class_incharge_year: int = 0,
                   class_incharge_section: str = "") -> dict:
    """Create a new faculty record."""
    _ensure_faculty_tables()
    import json
    subjects_json = json.dumps(subjects or [])
    with _conn() as c:
        try:
            c.execute(
                "INSERT INTO faculty"
                " (fac_id,name,dept,designation,email,mobile,subjects,joined_on,"
                "  class_incharge_dept,class_incharge_year,class_incharge_section)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (fac_id, name, dept, designation, email, mobile,
                 subjects_json, datetime.now().strftime("%Y-%m-%d"),
                 class_incharge_dept, class_incharge_year, class_incharge_section)
            )
            c.commit()
            return {"ok": True, "fac_id": fac_id}
        except sqlite3.IntegrityError:
            return {"ok": False, "error": "Faculty ID already exists"}


def export_faculty_csv(dept: str = None) -> str:
    """Return CSV string of all faculty + their 30-day attendance %."""
    rows = get_all_faculty(dept=dept)
    out  = io.StringIO()
    fields = ["fac_id", "name", "dept", "designation", "email",
              "mobile", "att_pct", "today_status"]
    w = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


# =============================================================
# ROUTE REGISTRATION  — called from api.py create_app()
# =============================================================

def register_feature_routes(app, get_current_user, teacher_required,
                             admin_required):
    """
    Mount all Feature 1 + Feature 2 API routes onto the FastAPI app.

    Call this at the bottom of create_app(), passing in the dependency
    functions already defined there.
    """
    from fastapi import Depends, Query, HTTPException
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel
    from typing import Optional

    # ── Pydantic models for faculty feature ─────────────────
    class FacultyAttReq(BaseModel):
        fac_id:       str
        att_date:     str
        status:       str
        arrival_time: Optional[str] = None
        reason:       Optional[str] = ""
        updated_by:   str = "ADMIN"

    class FacultyEditReq(BaseModel):
        status:       str
        arrival_time: Optional[str] = None
        reason:       Optional[str] = ""
        updated_by:   str = "ADMIN"

    class CreateFacultyReq(BaseModel):
        # Primary fields (used by admin faculty management UI)
        fac_id:      Optional[str] = ""
        name:        Optional[str] = ""
        dept:        Optional[str] = ""
        designation: Optional[str] = "Assistant Professor"
        email:       Optional[str] = ""
        mobile:      Optional[str] = ""
        subjects:    Optional[list] = []
        class_incharge_dept:    Optional[str] = ""
        class_incharge_year:    Optional[int] = 0
        class_incharge_section: Optional[str] = ""
        # Alias fields sent by the frontend enrollment form
        # (staff_id, first_name, last_name, department map to fac_id, name, dept)
        staff_id:            Optional[str] = ""
        first_name:          Optional[str] = ""
        last_name:           Optional[str] = ""
        department:          Optional[str] = ""
        gender:              Optional[str] = ""
        date_of_birth:       Optional[str] = ""
        joining_date:        Optional[str] = ""
        employee_code:       Optional[str] = ""
        role:                Optional[str] = "Faculty"
        is_class_incharge:   Optional[int] = 0
        incharge_department: Optional[str] = ""
        incharge_year:       Optional[str] = ""
        incharge_section:    Optional[str] = ""

    class StaffEnrollReq(BaseModel):
        """Payload sent by the frontend Faculty Enrollment form.
        Maps directly to the unified faculty table (staff table removed).
        Frontend sends: fac_id (primary), staff_id (legacy alias), first_name,
                        last_name, gender, date_of_birth, department, designation,
                        email, mobile, joining_date, employee_code,
                        role, is_class_incharge, incharge_department,
                        incharge_year, incharge_section
        """
        fac_id:              Optional[str] = ""   # primary — preferred key
        staff_id:            Optional[str] = ""   # legacy alias — accepted for compat
        first_name:          str
        last_name:           str
        gender:              Optional[str] = ""
        date_of_birth:       Optional[str] = ""
        department:          Optional[str] = ""
        designation:         Optional[str] = "Assistant Professor"
        email:               Optional[str] = ""
        mobile:              Optional[str] = ""
        joining_date:        Optional[str] = ""
        employee_code:       Optional[str] = ""
        role:                Optional[str] = "Faculty"
        is_class_incharge:   Optional[int] = 0
        incharge_department: Optional[str] = ""
        incharge_year:       Optional[str] = ""
        incharge_section:    Optional[str] = ""

    class FacultyUpdateReq(BaseModel):
        """Payload for PUT /api/faculty/{fac_id} — update faculty fields."""
        name:          Optional[str] = None
        dept:          Optional[str] = None
        department:    Optional[str] = None   # alias for dept
        designation:   Optional[str] = None
        email:         Optional[str] = None
        mobile:        Optional[str] = None
        joining_date:  Optional[str] = None
        employee_code: Optional[str] = None
        active:        Optional[int] = None

    # ── Ensure tables exist once at startup ─────────────────
    try:
        _ensure_faculty_tables()
    except Exception as e:
        log.error("faculty table init failed: %s", e)

    # ===========================================================
    # FEATURE 1 — DEPARTMENT DRILL-DOWN
    # ===========================================================

    @app.get("/api/departments")
    def api_departments(_: dict = Depends(get_current_user)):
        """
        Institution-level overview: all depts with live attendance stats.
        Also returns the DEPT_META taxonomy so the frontend can build
        dept/course/section dropdowns without extra round-trips.
        """
        try:
            depts = get_departments_overview()
            # Expose taxonomy (course names + sections) for the frontend
            taxonomy = {
                dk: {
                    "name":    dv["name"],
                    "emoji":   dv["emoji"],
                    "color":   dv["color"],
                    "courses": {
                        ck: {
                            "name":  cv["name"],
                            "secs":  _flat_secs(cv),
                            "years": list(cv.get("years", {}).keys()),
                        }
                        for ck, cv in dv["courses"].items()
                    }
                }
                for dk, dv in DEPT_META.items()
            }
            return {"departments": depts, "taxonomy": taxonomy}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── Attendance-filter cascade routes (PUBLIC — no auth required) ──────────
    # These four endpoints are called by the attendance kiosk filter dropdowns
    # (dept → course → year → sections) which run before any session starts.
    # Removing the auth dependency here is intentional and mirrors the pattern
    # used by the other public attendance endpoints (/api/role/session/*,
    # /api/enrollment/counts, /api/staff/by-dept, /api/hod/by-dept).

    @app.get("/api/departments/{dept_key}/courses")
    def api_dept_courses(dept_key: str):
        """Course-level breakdown for a single department. Public endpoint."""
        try:
            data = get_dept_courses(dept_key.upper())
            if not data:
                raise HTTPException(status_code=404,
                                    detail=f"Dept {dept_key} not found")
            return data
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/departments/{dept_key}/courses/{course_key}/sections")
    def api_course_sections(dept_key: str, course_key: str,
                             year: str = Query(None)):
        """Section-level breakdown for a dept/course. Public endpoint."""
        try:
            data = get_course_sections(dept_key.upper(), course_key.upper(),
                                       year.upper() if year else None)
            if not data:
                raise HTTPException(status_code=404, detail="Not found")
            return data
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/departments/{dept_key}/courses/{course_key}/years")
    def api_course_years(dept_key: str, course_key: str):
        """Return available academic years for a dept/course. Public endpoint."""
        try:
            data = get_course_years(dept_key.upper(), course_key.upper())
            if not data:
                raise HTTPException(status_code=404, detail="Not found")
            return data
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/departments/{dept_key}/courses/{course_key}"
             "/years/{year}/sections")
    def api_year_sections(dept_key: str, course_key: str, year: str):
        """Section list for a specific year within a dept/course. Public endpoint."""
        try:
            data = get_course_sections(dept_key.upper(), course_key.upper(),
                                       year.upper())
            if not data:
                raise HTTPException(status_code=404, detail="Not found")
            return data
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/departments/{dept_key}/courses/{course_key}"
             "/sections/{section}/students")
    def api_section_students(dept_key: str, course_key: str,
                              section: str, days: int = 30,
                              _: dict = Depends(get_current_user)):
        """Student list + per-student attendance % for a section."""
        try:
            data = get_section_students(dept_key.upper(),
                                        course_key.upper(),
                                        section.upper(), days)
            return data
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ===========================================================
    # FEATURE 2 — FACULTY MANAGEMENT
    # ===========================================================

    @app.get("/api/faculty/analytics/summary")
    def api_faculty_analytics(_: dict = Depends(get_current_user)):
        """KPI strip + chart data for the faculty management page."""
        try:
            return get_faculty_analytics()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/faculty")
    def api_faculty_list(dept:     str = Query(None),
                          search:   str = Query(None),
                          att_date: str = Query(None),
                          _: dict = Depends(get_current_user)):
        """Roster of all faculty, filterable by dept/search/date."""
        try:
            return get_all_faculty(dept=dept, search=search,
                                   att_date=att_date)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/staff/enroll")  # legacy alias — kept for backward compat
    def api_staff_enroll(req: StaffEnrollReq,
                         user: dict = Depends(get_current_user)):
        """
        Frontend Faculty/Staff Enrollment endpoint.
        POST /api/staff/enroll  ->  writes to unified faculty table.
        The legacy staff table has been removed; all records live in faculty.

        Accepts the exact payload the frontend sends:
            staff_id, first_name, last_name, gender, date_of_birth,
            department, designation, email, mobile, joining_date,
            employee_code, role, is_class_incharge,
            incharge_department, incharge_year, incharge_section
        """
        import database as _db
        # Accept fac_id (primary) or staff_id (legacy alias)
        sid = ((getattr(req, 'fac_id', '') or '') or (req.staff_id or '')).strip().upper()
        if not sid:
            raise HTTPException(status_code=400, detail="Faculty ID (fac_id) is required")

        # Strict name validation (backend)
        req.first_name = _validate_name_feat(req.first_name, "First Name")
        req.last_name  = _validate_name_feat(req.last_name,  "Last Name")
        # Strict mobile validation (backend)
        if req.mobile:
            req.mobile = _validate_mobile_feat(req.mobile, "Mobile Number", sid)
        # Strict DOB validation (backend, Step 7-8 of validation flow)
        req.date_of_birth = _validate_dob_feat(
            req.date_of_birth or "",
            "faculty",
            req.designation or "Assistant Professor"
        )
        full_name = f"{req.first_name} {req.last_name}".strip() or sid
        dept = (req.department or "").upper()

        ok = _db.add_staff(
            staff_id      = sid,
            employee_code = req.employee_code or "",
            first_name    = req.first_name,
            last_name     = req.last_name,
            gender        = req.gender or "",
            date_of_birth = req.date_of_birth or "",
            department    = dept,
            designation   = req.designation or "Assistant Professor",
            email         = req.email or "",
            mobile        = req.mobile or "",
            joining_date  = req.joining_date or "",
        )

        if not ok:
            # Already exists — if class-incharge info provided, update it
            if req.is_class_incharge and req.incharge_department:
                try:
                    with _conn() as c:
                        ic_year = int(req.incharge_year) if req.incharge_year else 0
                        c.execute("""
                            UPDATE faculty SET
                                is_class_incharge    = 1,
                                incharge_department  = ?,
                                incharge_year        = ?,
                                incharge_section     = ?,
                                class_incharge_dept  = ?,
                                class_incharge_year  = ?,
                                class_incharge_section = ?,
                                role = ?
                            WHERE fac_id = ?
                        """, (req.incharge_department, req.incharge_year,
                              req.incharge_section,
                              req.incharge_department, ic_year,
                              req.incharge_section,
                              req.role or "classincharge",
                              sid))
                except Exception:
                    pass
            raise HTTPException(
                status_code=409,
                detail=f"Faculty ID '{sid}' already exists"
            )

        # New record inserted — apply class-incharge fields if set
        if req.is_class_incharge and req.incharge_department:
            try:
                with _conn() as c:
                    ic_year = int(req.incharge_year) if req.incharge_year else 0
                    c.execute("""
                        UPDATE faculty SET
                            is_class_incharge    = 1,
                            incharge_department  = ?,
                            incharge_year        = ?,
                            incharge_section     = ?,
                            class_incharge_dept  = ?,
                            class_incharge_year  = ?,
                            class_incharge_section = ?,
                            role = ?
                        WHERE fac_id = ?
                    """, (req.incharge_department, req.incharge_year,
                          req.incharge_section,
                          req.incharge_department, ic_year,
                          req.incharge_section,
                          req.role or "classincharge",
                          sid))
            except Exception:
                pass

        return {
            "ok":     True,
            "fac_id": sid,
            "name":   full_name,
            "message": f"{full_name} enrolled successfully in faculty table"
        }

    @app.post("/api/faculty/enroll")
    def api_faculty_enroll(req: StaffEnrollReq,
                           user: dict = Depends(get_current_user)):
        """
        Primary faculty enrollment endpoint — unified under /api/faculty/enroll.
        Accepts fac_id (or staff_id as alias), first_name, last_name, gender,
        date_of_birth, department, designation, email, mobile, joining_date,
        employee_code, role, is_class_incharge, incharge_department,
        incharge_year, incharge_section.
        """
        import database as _db
        # Accept fac_id or staff_id — prefer fac_id
        fac_id_raw = (getattr(req, 'fac_id', '') or req.staff_id or '').strip().upper()
        if not fac_id_raw:
            raise HTTPException(status_code=400, detail="fac_id (Faculty ID) is required")

        # Reuse req.staff_id so the underlying add_staff path works unchanged
        req.staff_id = fac_id_raw

        # Strict name validation (backend)
        req.first_name = _validate_name_feat(req.first_name, "First Name")
        req.last_name  = _validate_name_feat(req.last_name,  "Last Name")
        # Strict mobile validation (backend)
        if req.mobile:
            req.mobile = _validate_mobile_feat(req.mobile, "Mobile Number", fac_id_raw)
        # Strict DOB validation (backend, Step 7-8 of validation flow)
        req.date_of_birth = _validate_dob_feat(
            req.date_of_birth or "",
            "faculty",
            req.designation or "Assistant Professor"
        )
        full_name = f"{req.first_name} {req.last_name}".strip() or fac_id_raw
        dept = (req.department or "").upper()

        ok = _db.add_staff(
            staff_id      = fac_id_raw,
            employee_code = req.employee_code or "",
            first_name    = req.first_name,
            last_name     = req.last_name,
            gender        = req.gender or "",
            date_of_birth = req.date_of_birth or "",
            department    = dept,
            designation   = req.designation or "Assistant Professor",
            email         = req.email or "",
            mobile        = req.mobile or "",
            joining_date  = req.joining_date or "",
        )

        if not ok:
            # Already exists — update class-incharge info if provided
            if req.is_class_incharge and req.incharge_department:
                try:
                    with _conn() as c:
                        ic_year = int(req.incharge_year) if req.incharge_year else 0
                        c.execute("""
                            UPDATE faculty SET
                                is_class_incharge    = 1,
                                incharge_department  = ?,
                                incharge_year        = ?,
                                incharge_section     = ?,
                                class_incharge_dept  = ?,
                                class_incharge_year  = ?,
                                class_incharge_section = ?,
                                role = ?
                            WHERE fac_id = ?
                        """, (req.incharge_department, req.incharge_year,
                              req.incharge_section,
                              req.incharge_department, ic_year,
                              req.incharge_section,
                              req.role or "classincharge",
                              fac_id_raw))
                except Exception:
                    pass
            raise HTTPException(
                status_code=409,
                detail=f"Faculty ID '{fac_id_raw}' already exists"
            )

        # Update class-incharge fields if provided on new enrollment
        if req.is_class_incharge and req.incharge_department:
            try:
                with _conn() as c:
                    ic_year = int(req.incharge_year) if req.incharge_year else 0
                    c.execute("""
                        UPDATE faculty SET
                            is_class_incharge    = 1,
                            incharge_department  = ?,
                            incharge_year        = ?,
                            incharge_section     = ?,
                            class_incharge_dept  = ?,
                            class_incharge_year  = ?,
                            class_incharge_section = ?,
                            role = ?
                        WHERE fac_id = ?
                    """, (req.incharge_department, req.incharge_year,
                          req.incharge_section,
                          req.incharge_department, ic_year,
                          req.incharge_section,
                          req.role or "classincharge",
                          fac_id_raw))
            except Exception:
                pass

        return {
            "ok":     True,
            "fac_id": fac_id_raw,
            "name":   full_name,
            "message": f"{full_name} enrolled successfully"
        }


    @app.post("/api/faculty")
    def api_create_faculty(req: CreateFacultyReq,
                            user: dict = Depends(admin_required)):
        """Create a new faculty record (admin/HOD only).
        Also accepts staff-enrollment field aliases (staff_id, first_name,
        last_name, department) so the frontend fallback path works correctly.
        """
        import database as _db
        try:
            # Resolve field aliases — the frontend enrollment form sends
            # staff_id / first_name / last_name / department instead of
            # fac_id / name / dept.  Normalise here so both paths work.
            fac_id = (req.fac_id or "").strip().upper()
            if not fac_id and req.staff_id:
                fac_id = req.staff_id.strip().upper()

            name = (req.name or "").strip()
            if not name:
                fn = _validate_name_feat(req.first_name or "", "First Name")
                ln = _validate_name_feat(req.last_name or "",  "Last Name")
                name = f"{fn} {ln}".strip() or fac_id

            dept = (req.dept or req.department or "").strip().upper()
            designation = req.designation or "Assistant Professor"

            # Strict DOB validation (backend, Step 7-8 of validation flow)
            dob_raw = getattr(req, "date_of_birth", "") or getattr(req, "dob", "") or ""
            if dob_raw:
                dob_raw = _validate_dob_feat(dob_raw, "faculty", designation)

            if not fac_id:
                raise HTTPException(status_code=422,
                                    detail="fac_id (or staff_id) is required")
            if not dept:
                raise HTTPException(status_code=422,
                                    detail="dept (or department) is required")

            res = create_faculty(
                fac_id=fac_id, name=name,
                dept=dept, designation=designation,
                email=req.email or "", mobile=req.mobile or "",
                subjects=req.subjects or [],
                class_incharge_dept=req.class_incharge_dept.upper() if req.class_incharge_dept else "",
                class_incharge_year=req.class_incharge_year or 0,
                class_incharge_section=req.class_incharge_section.upper() if req.class_incharge_section else "",
            )
            if not res["ok"]:
                raise HTTPException(status_code=409,
                                    detail=res.get("error", "Conflict"))
            return res
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/faculty/incharge")
    def api_faculty_incharge(dept: str = Query(None), year: int = Query(None),
                              section: str = Query(None),
                              _: dict = Depends(get_current_user)):
        """
        Find the class-incharge faculty for a dept/year/section.
        Pass dept+year+section to get a single incharge.
        Pass only dept (or nothing) to list all incharge assignments.
        """
        try:
            with _conn() as c:
                if dept and year and section:
                    row = c.execute(
                        "SELECT * FROM faculty WHERE class_incharge_dept=?"
                        " AND class_incharge_year=? AND class_incharge_section=?"
                        " AND active=1",
                        (dept.upper(), int(year), section.upper())
                    ).fetchone()
                    if not row:
                        return {"incharge": None}
                    import json as _json
                    r = dict(row)
                    r["subjects"] = _json.loads(r.get("subjects") or "[]")
                    return {"incharge": r}
                else:
                    sql = "SELECT * FROM faculty WHERE class_incharge_section != '' AND active=1"
                    params = []
                    if dept:
                        sql += " AND class_incharge_dept=?"; params.append(dept.upper())
                    rows = c.execute(sql, params).fetchall()
                    import json as _json
                    result = []
                    for row in rows:
                        r = dict(row)
                        r["subjects"] = _json.loads(r.get("subjects") or "[]")
                        result.append(r)
                    return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/faculty/export/csv")
    def api_faculty_export(dept: str = Query(None),
                            _: dict = Depends(get_current_user)):
        """Download faculty attendance CSV."""
        try:
            csv_str = export_faculty_csv(dept=dept)
            date_str = datetime.now().strftime("%Y-%m-%d")
            return StreamingResponse(
                iter([csv_str]),
                media_type="text/csv",
                headers={"Content-Disposition":
                         f"attachment; filename=faculty_att_{date_str}.csv"}
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/faculty/{fac_id}")
    def api_faculty_detail(fac_id: str, days: int = 30,
                            _: dict = Depends(get_current_user)):
        """Full profile + attendance history for one faculty member."""
        try:
            data = get_faculty_detail(fac_id.upper(), days)
            if not data:
                raise HTTPException(status_code=404,
                                    detail="Faculty not found")
            return data
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/faculty/{fac_id}")
    def api_delete_faculty(fac_id: str,
                            user: dict = Depends(admin_required)):
        """Delete a faculty/staff member (admin only).
        Removes from faculty table and cleans up related attendance records.
        Also removes from staff_attendance and staff_timetable tables.
        """
        import sqlite3, os
        try:
            import config as _cfg
            db_path = os.path.join(_cfg.BASE_DIR, "attendance.db")
            with sqlite3.connect(db_path) as conn:
                fid = fac_id.strip().upper()
                # Check existence — search both active and inactive records
                row = conn.execute(
                    "SELECT fac_id FROM faculty WHERE fac_id=?", (fid,)
                ).fetchone()
                if not row:
                    raise HTTPException(status_code=404,
                                        detail=f"Faculty '{fid}' not found")
                # Soft delete: mark inactive, preserve all records
                conn.execute(
                    "UPDATE faculty SET active=0 WHERE fac_id=?", (fid,)
                )
                conn.commit()
            return {"status": "deactivated", "fac_id": fid,
                    "message": "Faculty marked inactive. All records preserved."}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/api/faculty/{fac_id}")
    def api_update_faculty(fac_id: str, req: FacultyUpdateReq,
                            user: dict = Depends(admin_required)):
        """Update faculty record fields (admin only).
        Accepts any subset of: name, dept, designation, email, mobile,
        joining_date, employee_code, active.
        """
        import sqlite3, os
        try:
            import config as _cfg
            db_path = os.path.join(_cfg.BASE_DIR, "attendance.db")
            with sqlite3.connect(db_path) as conn:
                fid = fac_id.strip().upper()
                row = conn.execute(
                    "SELECT fac_id FROM faculty WHERE fac_id=?", (fid,)
                ).fetchone()
                if not row:
                    raise HTTPException(status_code=404,
                                        detail=f"Faculty '{fid}' not found")
                # Build update dict from non-None fields only
                raw = req.dict(exclude_none=True)
                # Alias: department → dept
                if "department" in raw:
                    raw["dept"] = raw.pop("department")
                allowed = {"name", "dept", "designation", "email",
                           "mobile", "joining_date", "employee_code", "active"}
                updates = {k: v for k, v in raw.items() if k in allowed and v != ""}
                if not updates:
                    raise HTTPException(status_code=422,
                                        detail="No valid fields to update")
                set_clause = ", ".join(f"{k}=?" for k in updates)
                values = list(updates.values()) + [fid]
                conn.execute(
                    f"UPDATE faculty SET {set_clause} WHERE fac_id=?", values
                )
                conn.commit()
            return {"status": "updated", "fac_id": fid}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/faculty/attendance")
    def api_mark_faculty_att(req: FacultyAttReq,
                              user: dict = Depends(teacher_required)):
        """Mark or update faculty attendance for a specific date."""
        try:
            res = mark_faculty_attendance(
                fac_id=req.fac_id.upper(), att_date=req.att_date,
                status=req.status, arrival_time=req.arrival_time,
                reason=req.reason, updated_by=req.updated_by
            )
            if not res["ok"]:
                raise HTTPException(status_code=404,
                                    detail=res.get("error"))
            return {"status": "saved", **res}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/api/faculty/{fac_id}/attendance/{log_id}")
    def api_edit_faculty_att(fac_id: str, log_id: int,
                              req: FacultyEditReq,
                              user: dict = Depends(teacher_required)):
        """Edit an existing faculty attendance log entry."""
        try:
            res = edit_faculty_attendance(
                log_id=log_id, status=req.status,
                arrival_time=req.arrival_time, reason=req.reason,
                updated_by=req.updated_by
            )
            if not res["ok"]:
                raise HTTPException(status_code=404,
                                    detail=res.get("error"))
            return {"status": "updated"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/faculty/{fac_id}/attendance/{log_id}")
    def api_delete_faculty_att(fac_id: str, log_id: int,
                                user: dict = Depends(admin_required)):
        """Delete a faculty attendance log entry (admin only)."""
        try:
            res = delete_faculty_attendance(log_id)
            if not res["ok"]:
                raise HTTPException(status_code=404,
                                    detail=res.get("error"))
            return {"status": "deleted"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ===========================================================
    # FEATURE 3 — DEPARTMENT DRILL-DOWN v2 (Reference Image Flow)
    # Flow: Software/Hardware → Dept → Year → Semester → Class → Subject → Student → Detail
    # ===========================================================

    # ── Taxonomy: Software vs Hardware departments ────────────────
    SW_DEPTS  = ["CSE", "AIDS", "IT", "CSBS", "CS_IT", "MCA", "BCA"]
    HW_DEPTS  = ["ECE", "EEE", "BM", "MECH", "CIVIL"]

    # Subject taxonomy per dept/year/semester
    SUBJECT_MAP = {
        "CSE": {
            1: {1: ["Mathematics I","Physics","C Programming","English","EVS"],
                2: ["Mathematics II","Chemistry","Data Structures","Engineering Drawing","Life Skills"]},
            2: {1: ["Data Structures & Algorithms","DBMS","Computer Organization","OOP with Java","Discrete Mathematics"],
                2: ["Operating Systems","Computer Networks","Software Engineering","Web Technology","Design & Analysis of Algorithms"]},
            3: {1: ["Compiler Design","Cryptography","AI","Cloud Computing","Mobile Computing"],
                2: ["Machine Learning","Big Data","IoT","Deep Learning","Elective I"]},
            4: {1: ["Project Phase I","Elective II","Elective III","Professional Ethics","Open Elective"],
                2: ["Project Phase II","Entrepreneurship","Technical Seminar"]},
        },
        "AIDS": {
            1: {1: ["Mathematics I","Statistics","Python Programming","English","EVS"],
                2: ["Mathematics II","Probability & Distributions","Data Wrangling","Technical Communication","Life Skills"]},
            2: {1: ["Machine Learning","Data Visualization","Database Systems","R Programming","Linear Algebra"],
                2: ["Deep Learning","NLP","Time Series Analysis","Cloud for AI","Ethics in AI"]},
            3: {1: ["Computer Vision","Reinforcement Learning","Big Data Analytics","MLOps","Elective I"],
                2: ["Generative AI","Edge AI","AI in Healthcare","Research Methods","Elective II"]},
            4: {1: ["Project Phase I","Elective III","Professional Ethics","Open Elective","Seminar"],
                2: ["Project Phase II","Entrepreneurship","Technical Presentation"]},
        },
        "IT": {
            1: {1: ["Mathematics I","Physics","Programming in C","English","EVS"],
                2: ["Mathematics II","Chemistry","Object Oriented Programming","Digital Electronics","Life Skills"]},
            2: {1: ["Data Structures","DBMS","Computer Networks","Software Engineering","Discrete Maths"],
                2: ["Operating Systems","Web Programming","OOP with Java","Design Patterns","Computer Architecture"]},
            3: {1: ["Information Security","Cloud Computing","IoT","Mobile Application Dev","Elective I"],
                2: ["Distributed Systems","DevOps","Blockchain","Microservices","Elective II"]},
            4: {1: ["Project Phase I","Elective III","Professional Ethics","Open Elective","Seminar"],
                2: ["Project Phase II","Entrepreneurship","Technical Seminar"]},
        },
        "CSBS": {
            1: {1: ["Mathematics I","Business Economics","Programming in C","English","EVS"],
                2: ["Statistics for Business","Accounting","OOP with Python","Marketing","Life Skills"]},
            2: {1: ["Data Structures","Financial Management","DBMS","Supply Chain","Business Analytics"],
                2: ["Machine Learning","Business Intelligence","ERP Systems","Operations Research","Web Technology"]},
            3: {1: ["Big Data","Digital Marketing","Cloud Computing","IoT for Business","Elective I"],
                2: ["AI for Business","CRM","Business Process Management","Capstone Project I","Elective II"]},
            4: {1: ["Project Phase I","Strategic Management","Professional Ethics","Open Elective","Seminar"],
                2: ["Project Phase II","Entrepreneurship","Technical Presentation"]},
        },
    }

    # Fall-back generic subjects
    _GENERIC_SUBS = {
        1: {1: ["Mathematics I","Physics","Programming Fundamentals","English","EVS"],
            2: ["Mathematics II","Chemistry","Applied Sciences","Technical Communication","Life Skills"]},
        2: {1: ["Core Subject I","Core Subject II","Core Subject III","Elective I","Lab I"],
            2: ["Core Subject IV","Core Subject V","Core Subject VI","Elective II","Lab II"]},
        3: {1: ["Advanced I","Advanced II","Advanced III","Project Lab","Elective III"],
            2: ["Advanced IV","Advanced V","Seminar","Project Phase I","Elective IV"]},
        4: {1: ["Project Phase I","Elective V","Professional Ethics","Open Elective","Seminar"],
            2: ["Project Phase II","Entrepreneurship","Technical Presentation"]},
    }

    def _get_subjects(dept_key, year, semester):
        dept_map = SUBJECT_MAP.get(dept_key.upper(), _GENERIC_SUBS)
        return dept_map.get(int(year), _GENERIC_SUBS.get(int(year), {})).get(int(semester), [])

    def _class_list(dept_key, year):
        """Return list of classes (sections) for dept + year from DEPT_META."""
        meta = DEPT_META.get(dept_key.upper(), {})
        year_labels = {"1": "I", "2": "II", "3": "III", "4": "IV"}
        yr_roman    = year_labels.get(str(year), str(year))
        classes = set()
        for ck, cv in meta.get("courses", {}).items():
            secs = cv.get("years", {}).get(yr_roman, [])
            classes.update(secs)
        return sorted(classes) or ["A", "B", "C"]

    def _working_days(days=30):
        return max(sum(
            1 for i in range(days)
            if (datetime.now() - timedelta(days=i)).weekday() < 5
        ), 1)

    def _student_att_for_period(student_id, days=30):
        cutoff  = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        working = _working_days(days)
        with _conn() as c:
            row = c.execute(
                "SELECT COUNT(DISTINCT date) AS p FROM attendance"
                " WHERE student_id=? AND date>=?", (student_id, cutoff)
            ).fetchone()
            p = row["p"] if row else 0
        return round(p / working * 100, 1)

    def _section_avg(section, days=30):
        cutoff  = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        working = _working_days(days)
        with _conn() as c:
            students = c.execute(
                "SELECT student_id FROM students WHERE active=1 AND section=?",
                (section,)
            ).fetchall()
            if not students:
                return 0.0
            total = 0.0
            for s in students:
                row = c.execute(
                    "SELECT COUNT(DISTINCT date) AS p FROM attendance"
                    " WHERE student_id=? AND date>=?", (s["student_id"], cutoff)
                ).fetchone()
                p = row["p"] if row else 0
                total += round(p / working * 100, 1)
            return round(total / len(students), 1)

    # ── GET /api/v2/categories ────────────────────────────────────
    @app.get("/api/v2/categories")
    def api_v2_categories(_: dict = Depends(get_current_user)):
        """Return Software and Hardware department categories with stats."""
        try:
            all_depts = get_departments_overview()
            dept_map  = {d["key"]: d for d in all_depts}

            def _cat_stats(keys):
                rows    = [dept_map[k] for k in keys if k in dept_map]
                total_s = sum(d["total_students"] for d in rows)
                avg_a   = round(sum(d["avg_att"] for d in rows) / max(len(rows), 1), 1)
                dcount  = len(rows)
                return {"total_students": total_s, "avg_att": avg_a, "dept_count": dcount}

            sw_stats = _cat_stats(SW_DEPTS)
            hw_stats = _cat_stats(HW_DEPTS)
            return {
                "software": {
                    "label": "Software",
                    "dept_count": sw_stats["dept_count"],
                    "total_students": sw_stats["total_students"],
                    "avg_att": sw_stats["avg_att"],
                    "dept_keys": SW_DEPTS,
                },
                "hardware": {
                    "label": "Hardware",
                    "dept_count": hw_stats["dept_count"],
                    "total_students": hw_stats["total_students"],
                    "avg_att": hw_stats["avg_att"],
                    "dept_keys": HW_DEPTS,
                },
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── GET /api/v2/departments?category=software ─────────────────
    @app.get("/api/v2/departments")
    def api_v2_departments(category: str = None,
                            _: dict = Depends(get_current_user)):
        """Return department list filtered by software/hardware category."""
        try:
            all_depts = get_departments_overview()
            if category == "software":
                all_depts = [d for d in all_depts if d["key"] in SW_DEPTS]
            elif category == "hardware":
                all_depts = [d for d in all_depts if d["key"] in HW_DEPTS]
            return {"departments": all_depts, "taxonomy": {
                dk: {
                    "name":    dv["name"],
                    "emoji":   dv["emoji"],
                    "color":   dv["color"],
                    "courses": {
                        ck: {"name": cv["name"], "secs": _flat_secs(cv),
                             "years": list(cv.get("years", {}).keys())}
                        for ck, cv in dv["courses"].items()
                    }
                }
                for dk, dv in DEPT_META.items()
            }}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── GET /api/v2/departments/{dept}/years ──────────────────────
    @app.get("/api/v2/departments/{dept_key}/years")
    def api_v2_dept_years(dept_key: str, _: dict = Depends(get_current_user)):
        """Year-wise attendance breakdown for a department."""
        try:
            dept_key = dept_key.upper()
            meta     = DEPT_META.get(dept_key)
            if not meta:
                raise HTTPException(status_code=404, detail="Dept not found")

            years = [{"num": 1, "label": "1st Year", "roman": "I"},
                     {"num": 2, "label": "2nd Year", "roman": "II"},
                     {"num": 3, "label": "3rd Year", "roman": "III"},
                     {"num": 4, "label": "4th Year", "roman": "IV"}]

            result = []
            for yr in years:
                classes = _class_list(dept_key, yr["num"])
                total_s = 0; att_sum = 0.0; good = warn = poor = 0
                for cls in classes:
                    with _conn() as c:
                        students = c.execute(
                            "SELECT student_id FROM students WHERE active=1 AND section=?",
                            (cls,)
                        ).fetchall()
                    total_s += len(students)
                    for s in students:
                        p = _student_att_for_period(s["student_id"])
                        att_sum += p
                        if p >= 75: good += 1
                        elif p >= 65: warn += 1
                        else: poor += 1

                avg_att = round(att_sum / max(total_s, 1), 1)
                result.append({
                    "year_num":  yr["num"],
                    "year_label": yr["label"],
                    "roman":     yr["roman"],
                    "classes":   classes,
                    "total_students": total_s,
                    "avg_att":   avg_att,
                    "good": good, "warn": warn, "poor": poor,
                })

            return {
                "dept_key":  dept_key,
                "dept_name": meta["name"],
                "dept_color": meta.get("color", "#4ecba8"),
                "years":     result,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── GET /api/v2/departments/{dept}/years/{year}/semesters ─────
    @app.get("/api/v2/departments/{dept_key}/years/{year}/semesters")
    def api_v2_semesters(dept_key: str, year: int,
                          _: dict = Depends(get_current_user)):
        """Semester-wise attendance for a dept/year."""
        try:
            dept_key = dept_key.upper()
            meta     = DEPT_META.get(dept_key)
            if not meta:
                raise HTTPException(status_code=404, detail="Dept not found")

            classes  = _class_list(dept_key, year)
            semesters = [
                {"num": (year - 1) * 2 + 1, "label": f"Semester {(year-1)*2+1}"},
                {"num": (year - 1) * 2 + 2, "label": f"Semester {(year-1)*2+2}"},
            ]

            result = []
            for sem in semesters:
                total_s = 0; att_sum = 0.0; good = warn = poor = 0
                subjects = _get_subjects(dept_key, year, sem["num"] % 2 or 2)
                for cls in classes:
                    with _conn() as c:
                        students = c.execute(
                            "SELECT student_id FROM students WHERE active=1 AND section=?",
                            (cls,)
                        ).fetchall()
                    total_s += len(students)
                    for s in students:
                        p = _student_att_for_period(s["student_id"])
                        att_sum += p
                        if p >= 75: good += 1
                        elif p >= 65: warn += 1
                        else: poor += 1

                avg_att = round(att_sum / max(total_s, 1), 1)
                result.append({
                    "sem_num":  sem["num"],
                    "sem_label": sem["label"],
                    "subjects": subjects,
                    "subject_count": len(subjects),
                    "classes":  classes,
                    "total_students": total_s,
                    "avg_att":  avg_att,
                    "good": good, "warn": warn, "poor": poor,
                })

            return {
                "dept_key":  dept_key,
                "dept_name": meta["name"],
                "dept_color": meta.get("color", "#4ecba8"),
                "year":      year,
                "semesters": result,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── GET /api/v2/departments/{dept}/years/{year}/semesters/{sem}/classes ──
    @app.get("/api/v2/departments/{dept_key}/years/{year}/semesters/{sem}/classes")
    def api_v2_classes(dept_key: str, year: int, sem: int,
                        _: dict = Depends(get_current_user)):
        """Class (section) wise attendance for dept/year/semester."""
        try:
            dept_key = dept_key.upper()
            meta     = DEPT_META.get(dept_key)
            if not meta:
                raise HTTPException(status_code=404, detail="Dept not found")

            classes = _class_list(dept_key, year)
            result  = []
            for cls in classes:
                with _conn() as c:
                    students = c.execute(
                        "SELECT student_id FROM students WHERE active=1 AND section=?",
                        (cls,)
                    ).fetchall()
                total_s = len(students)
                att_sum = 0.0; good = warn = poor = 0
                for s in students:
                    p = _student_att_for_period(s["student_id"])
                    att_sum += p
                    if p >= 75: good += 1
                    elif p >= 65: warn += 1
                    else: poor += 1

                avg_att = round(att_sum / max(total_s, 1), 1)
                result.append({
                    "class_label": f"Class {cls}",
                    "section":     cls,
                    "total_students": total_s,
                    "avg_att":     avg_att,
                    "good": good, "warn": warn, "poor": poor,
                })

            subjects = _get_subjects(dept_key, year, sem % 2 or 2)
            return {
                "dept_key":  dept_key,
                "dept_name": meta["name"],
                "dept_color": meta.get("color", "#4ecba8"),
                "year":      year,
                "sem":       sem,
                "subjects":  subjects,
                "classes":   result,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── GET /api/v2/departments/{dept}/years/{year}/semesters/{sem}/classes/{cls}/subjects ──
    @app.get("/api/v2/departments/{dept_key}/years/{year}/semesters/{sem}/classes/{cls}/subjects")
    def api_v2_subjects(dept_key: str, year: int, sem: int, cls: str,
                         _: dict = Depends(get_current_user)):
        """Subject-wise attendance for a class (simulated from overall student att)."""
        try:
            dept_key = dept_key.upper()
            cls      = cls.upper()
            meta     = DEPT_META.get(dept_key)
            if not meta:
                raise HTTPException(status_code=404, detail="Dept not found")

            sem_offset = sem % 2 or 2
            subjects   = _get_subjects(dept_key, year, sem_offset)
            if not subjects:
                subjects = [f"Subject {i+1}" for i in range(5)]

            with _conn() as c:
                students = c.execute(
                    "SELECT student_id FROM students WHERE active=1 AND section=?",
                    (cls,)
                ).fetchall()
            total_s = len(students)

            # Simulate subject-wise attendance with slight variation
            import random, hashlib
            result = []
            base_avg = _section_avg(cls) if students else 0.0
            for idx, subj in enumerate(subjects):
                # Deterministic variation per subject (so refreshing is consistent)
                seed_val = int(hashlib.md5(f"{dept_key}{year}{sem}{cls}{subj}".encode()).hexdigest()[:8], 16)
                rng = random.Random(seed_val)
                variation = rng.uniform(-8, 8)
                avg_att = max(0, min(100, round(base_avg + variation, 1)))
                good = int(total_s * (avg_att / 100) * rng.uniform(0.85, 1.0))
                poor = max(0, int(total_s * (1 - avg_att / 100) * rng.uniform(0.5, 0.9)))
                warn = max(0, total_s - good - poor)
                result.append({
                    "subject":  subj,
                    "avg_att":  avg_att,
                    "total":    total_s,
                    "good":     good,
                    "warn":     warn,
                    "poor":     poor,
                })

            return {
                "dept_key":  dept_key,
                "dept_name": meta["name"],
                "dept_color": meta.get("color", "#4ecba8"),
                "year": year, "sem": sem, "class": cls,
                "total_students": total_s,
                "subjects":  result,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── GET /api/v2/departments/{dept}/years/{year}/semesters/{sem}/classes/{cls}/subjects/{subj}/students ──
    @app.get("/api/v2/departments/{dept_key}/years/{year}/semesters/{sem}/classes/{cls}/subjects/{subj}/students")
    def api_v2_subject_students(dept_key: str, year: int, sem: int,
                                 cls: str, subj: str,
                                 _: dict = Depends(get_current_user)):
        """Student-wise attendance for a specific subject in a class."""
        try:
            dept_key = dept_key.upper()
            cls      = cls.upper()
            days     = 30
            cutoff   = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            working  = _working_days(days)

            with _conn() as c:
                students = c.execute(
                    "SELECT student_id, name, roll_number FROM students"
                    " WHERE active=1 AND section=? ORDER BY name",
                    (cls,)
                ).fetchall()

            import random, hashlib
            result = []
            seed_val = int(hashlib.md5(f"{dept_key}{year}{sem}{cls}{subj}".encode()).hexdigest()[:8], 16)
            rng = random.Random(seed_val)

            for idx, s in enumerate(students):
                # Base: real attendance + subject-specific variation
                with _conn() as c:
                    row = c.execute(
                        "SELECT COUNT(DISTINCT date) AS p FROM attendance"
                        " WHERE student_id=? AND date>=?", (s["student_id"], cutoff)
                    ).fetchone()
                base_present = row["p"] if row else 0
                variation    = rng.randint(-3, 3)
                present      = max(0, min(working, base_present + variation))
                att_pct      = round(present / working * 100, 1)
                result.append({
                    "student_id":  s["student_id"],
                    "name":        s["name"] or "?",
                    "roll_number": s["roll_number"] or s["student_id"],
                    "present":     present,
                    "total":       working,
                    "att_pct":     att_pct,
                    "status":      "good" if att_pct >= 75 else "warn" if att_pct >= 65 else "poor",
                })

            total  = len(result)
            avg_a  = round(sum(s["att_pct"] for s in result) / max(total, 1), 1)
            good   = sum(1 for s in result if s["status"] == "good")
            warn   = sum(1 for s in result if s["status"] == "warn")
            poor   = sum(1 for s in result if s["status"] == "poor")

            return {
                "dept_key": dept_key,
                "year": year, "sem": sem, "class": cls, "subject": subj,
                "dept_color": DEPT_META.get(dept_key, {}).get("color", "#4ecba8"),
                "classes_held": working,
                "classes_attended_avg": int(avg_a * working / 100),
                "students": result,
                "stats": {
                    "total": total, "avg_att": avg_a,
                    "good": good, "warn": warn, "poor": poor,
                },
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── GET /api/v2/students/{student_id}/subjects/{subj}/detail ──
    @app.get("/api/v2/students/{student_id}/subjects/{subj}/detail")
    def api_v2_student_detail(student_id: str, subj: str,
                               _: dict = Depends(get_current_user)):
        """Detailed attendance for one student in one subject."""
        try:
            days    = 30
            cutoff  = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            working = _working_days(days)

            with _conn() as c:
                stu = c.execute(
                    "SELECT * FROM students WHERE student_id=? AND active=1",
                    (student_id,)
                ).fetchone()
                if not stu:
                    raise HTTPException(status_code=404, detail="Student not found")
                stu = dict(stu)

                att_rows = c.execute(
                    "SELECT date, period, confidence FROM attendance"
                    " WHERE student_id=? AND date>=? ORDER BY date DESC",
                    (student_id, cutoff)
                ).fetchall()

            # Build topic schedule (simulated for subject)
            import random, hashlib
            seed_v = int(hashlib.md5(f"{student_id}{subj}".encode()).hexdigest()[:8], 16)
            rng    = random.Random(seed_v)

            topic_pool = {
                "Mathematics I": ["Algebra Basics","Linear Equations","Quadratic Equations",
                                   "Functions","Polynomials","Limits","Differentiation",
                                   "Integration","Matrices","Vectors"],
                "default": [f"{subj} - Topic {i+1}" for i in range(10)],
            }
            topics = topic_pool.get(subj, topic_pool["default"])

            # Use real attendance dates if available, else simulate
            detail_log = []
            if att_rows:
                seen_dates = []
                for ar in att_rows[:10]:
                    d = ar["date"]
                    if d not in seen_dates:
                        seen_dates.append(d)
                        t = topics[len(seen_dates) % len(topics)]
                        detail_log.append({
                            "date": d, "topic": t, "status": "Present",
                        })
                # Add some absent days
                all_working = []
                for i in range(days):
                    d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
                    if datetime.strptime(d, "%Y-%m-%d").weekday() < 5:
                        all_working.append(d)
                for d in all_working[:10]:
                    if d not in seen_dates:
                        t = topics[len(detail_log) % len(topics)]
                        detail_log.append({
                            "date": d, "topic": t, "status": "Absent",
                        })
            else:
                for i in range(10):
                    d = (datetime.now() - timedelta(days=(i+1)*2)).strftime("%Y-%m-%d")
                    if datetime.strptime(d, "%Y-%m-%d").weekday() >= 5:
                        continue
                    st = "Present" if rng.random() > 0.25 else "Absent"
                    t  = topics[i % len(topics)]
                    detail_log.append({"date": d, "topic": t, "status": st})

            detail_log.sort(key=lambda x: x["date"])
            present   = sum(1 for l in detail_log if l["status"] == "Present")
            classes_h = len(detail_log) or working
            att_pct   = round(present / max(classes_h, 1) * 100, 1)

            # Weekly trend (W1..W10)
            weekly = []
            for w in range(1, 11):
                weekly.append({"week": f"W{w}", "pct": min(100, max(0,
                    round(att_pct + rng.uniform(-10, 10), 1)))})

            return {
                "student_id":    student_id,
                "name":          stu.get("name", "?"),
                "register_no":   stu.get("roll_number", student_id),
                "section":       stu.get("section", "?"),
                "subject":       subj,
                "overall_att":   att_pct,
                "classes_held":  classes_h,
                "classes_attended": present,
                "detail_log":    detail_log,
                "weekly_trend":  weekly,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── EduDrill Pro analytics routes ─────────────────────────────
    try:
        from edudrill_routes import register_edudrill_routes
        register_edudrill_routes(app, get_current_user)
    except Exception as _ed_err:
        log.warning("EduDrill routes not loaded: %s", _ed_err)

    log.info("Feature routes (departments + faculty + EduDrill) registered.")