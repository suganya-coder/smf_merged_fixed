# =============================================================
# edudrill_routes.py  —  EduDrill Pro analytics routes
# Called from api_features.register_feature_routes()
# =============================================================

import sqlite3
import random
import logging
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)


def _db_path():
    import config
    import os
    return os.path.join(config.BASE_DIR, "attendance.db")


def _conn():
    c = sqlite3.connect(_db_path(), timeout=15, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


# ── Shared taxonomy (matches DEPT_META in api_features.py) ────
DRILL_DEPT_META = {
    "CS":    {"name": "Computer Science",       "emoji": "💻", "color": "#4ecba8", "accent": "#34d399",
              "courses": {
                  "DS":   {"name": "Data Structures",        "secs": ["A","B"], "hod": "Dr. A. Kumar"},
                  "AI":   {"name": "Artificial Intelligence","secs": ["A"],     "hod": "Dr. A. Kumar"},
                  "WEB":  {"name": "Web Technology",         "secs": ["A","B"], "hod": "Ms. R. Priya"},
                  "DBMS": {"name": "Database Systems",       "secs": ["A"],     "hod": "Ms. R. Priya"},
              }},
    "ECE":   {"name": "Electronics & Comm",     "emoji": "📡", "color": "#4da6f5", "accent": "#60a5fa",
              "courses": {
                  "DSP":  {"name": "Digital Signal Processing","secs": ["A"],     "hod": "Dr. S. Rajan"},
                  "VLSI": {"name": "VLSI Design",               "secs": ["A","B"], "hod": "Dr. S. Rajan"},
                  "ES":   {"name": "Embedded Systems",          "secs": ["A"],     "hod": "Mr. K. Venkat"},
              }},
    "MECH":  {"name": "Mechanical Engineering", "emoji": "⚙️",  "color": "#ffb347", "accent": "#fb923c",
              "courses": {
                  "TD":   {"name": "Thermodynamics",  "secs": ["A","B"], "hod": "Dr. M. Lakshmi"},
                  "FM":   {"name": "Fluid Mechanics",  "secs": ["A"],     "hod": "Dr. M. Lakshmi"},
                  "CAD":  {"name": "CAD/CAM",           "secs": ["A"],     "hod": "Ms. P. Deepa"},
              }},
    "CIVIL": {"name": "Civil Engineering",      "emoji": "🏗️",  "color": "#9b87f5", "accent": "#a78bfa",
              "courses": {
                  "SA":   {"name": "Structural Analysis","secs": ["A"],   "hod": "Dr. T. Suresh"},
                  "RCC":  {"name": "RCC Design",          "secs": ["A"],  "hod": "Dr. T. Suresh"},
              }},
    "IT":    {"name": "Information Technology", "emoji": "🖧",  "color": "#ff7070", "accent": "#f87171",
              "courses": {
                  "PY":   {"name": "Python Programming",  "secs": ["A","B"],"hod": "Mr. G. Balamurugan"},
                  "NET":  {"name": "Computer Networks",   "secs": ["A"],    "hod": "Mr. G. Balamurugan"},
                  "OS":   {"name": "Operating Systems",   "secs": ["A"],    "hod": "Mr. G. Balamurugan"},
              }},
}

# Map roll_number prefixes → dept key  (e.g. "23CS086" → "CS")
def _roll_to_dept(roll: str) -> Optional[str]:
    roll = (roll or "").upper()
    for dk in DRILL_DEPT_META:
        if dk in roll:
            return dk
    return None


def _working_days(days: int = 30) -> int:
    return max(sum(1 for i in range(days)
                   if (datetime.now() - timedelta(days=i)).weekday() < 5), 1)


# ── DB queries ─────────────────────────────────────────────────

def _student_count_for_section(conn, section: str) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM students WHERE active=1 AND section=?",
        (section,)
    ).fetchone()
    return row["n"] if row else 0


def _attendance_pct_for_section(conn, section: str, days: int = 30) -> float:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    wdays  = _working_days(days)
    students = conn.execute(
        "SELECT student_id FROM students WHERE active=1 AND section=?",
        (section,)
    ).fetchall()
    if not students:
        return 0.0
    total_possible = len(students) * wdays
    present = conn.execute(
        "SELECT COUNT(*) AS n FROM attendance a "
        "JOIN students s ON s.student_id=a.student_id "
        "WHERE s.active=1 AND s.section=? AND a.date>=?",
        (section, cutoff)
    ).fetchone()["n"]
    return round(present / total_possible * 100, 1) if total_possible else 0.0


def _today_present_for_section(conn, section: str) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COUNT(DISTINCT a.student_id) AS n FROM attendance a "
        "JOIN students s ON s.student_id=a.student_id "
        "WHERE s.section=? AND a.date=?",
        (section, today)
    ).fetchone()
    return row["n"] if row else 0


def _faculty_count_for_dept(conn, dept_key: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM faculty WHERE active=1 AND dept=?",
        (dept_key,)
    ).fetchone()
    return row["n"] if row else 0


def _all_students_for_section(conn, section: str, days: int = 30) -> list:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    wdays  = _working_days(days)
    students = conn.execute(
        "SELECT student_id, name, roll_number, mobile, section, enrolled_on "
        "FROM students WHERE active=1 AND section=? ORDER BY name",
        (section,)
    ).fetchall()
    result = []
    for s in students:
        row = conn.execute(
            "SELECT COUNT(DISTINCT date) AS p, MAX(date) AS last "
            "FROM attendance WHERE student_id=? AND date>=?",
            (s["student_id"], cutoff)
        ).fetchone()
        p    = row["p"] if row else 0
        last = (row["last"] or "")[:10] if row else ""
        pct  = round(p / wdays * 100, 1)
        # Derive dept from roll_number
        dept = _roll_to_dept(s["roll_number"] or "")
        result.append({
            "student_id":  s["student_id"],
            "name":        s["name"] or "—",
            "roll_no":     s["roll_number"] or s["student_id"],
            "mobile":      s["mobile"] or "—",
            "section":     s["section"] or section,
            "enrolled_on": (s["enrolled_on"] or "")[:10],
            "dept":        dept or "—",
            "present":     p,
            "total":       wdays,
            "att_pct":     pct,
            "last_seen":   last,
            "status":      "good" if pct >= 75 else ("warn" if pct >= 65 else "poor"),
        })
    return result


def _faculty_for_dept(conn, dept_key: str) -> list:
    faculty = conn.execute(
        "SELECT fac_id, name, dept, designation, email, mobile, subjects "
        "FROM faculty WHERE active=1 AND dept=? ORDER BY name",
        (dept_key,)
    ).fetchall()
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    wdays  = _working_days(30)
    result = []
    for f in faculty:
        row = conn.execute(
            "SELECT COUNT(*) AS p FROM faculty_attendance "
            "WHERE fac_id=? AND status IN ('present','late','od') AND att_date>=?",
            (f["fac_id"], cutoff)
        ).fetchone()
        p   = row["p"] if row else 0
        pct = round(p / wdays * 100, 1)
        import json
        try:
            subj = json.loads(f["subjects"] or "[]")
        except Exception:
            subj = []
        result.append({
            "fac_id":      f["fac_id"],
            "name":        f["name"] or "—",
            "dept":        f["dept"],
            "designation": f["designation"] or "—",
            "email":       f["email"] or "—",
            "mobile":      f["mobile"] or "—",
            "subjects":    subj,
            "att_pct":     pct,
        })
    return result


# ── Overview ───────────────────────────────────────────────────

def get_edudrill_overview() -> dict:
    """Institution-wide KPI summary for EduDrill home page."""
    with _conn() as conn:
        total_students = conn.execute(
            "SELECT COUNT(*) AS n FROM students WHERE active=1"
        ).fetchone()["n"]
        total_faculty = conn.execute(
            "SELECT COUNT(*) AS n FROM faculty WHERE active=1"
        ).fetchone()["n"] if _table_exists(conn, "faculty") else 0
        today = datetime.now().strftime("%Y-%m-%d")
        present_today = conn.execute(
            "SELECT COUNT(DISTINCT student_id) AS n FROM attendance WHERE date=?",
            (today,)
        ).fetchone()["n"]
        absent_today = max(0, total_students - present_today)
        pct_today = round(present_today / total_students * 100, 1) if total_students else 0

        # 30-day avg
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        wdays  = _working_days(30)
        total_possible = total_students * wdays
        total_present  = conn.execute(
            "SELECT COUNT(*) AS n FROM attendance WHERE date>=?", (cutoff,)
        ).fetchone()["n"]
        avg_att = round(total_present / total_possible * 100, 1) if total_possible else 0

        # Department count
        total_depts = len(DRILL_DEPT_META)
        total_courses = sum(len(v["courses"]) for v in DRILL_DEPT_META.values())

        return {
            "total_students":  total_students,
            "total_faculty":   total_faculty,
            "present_today":   present_today,
            "absent_today":    absent_today,
            "pct_today":       pct_today,
            "avg_attendance":  avg_att,
            "total_depts":     total_depts,
            "total_courses":   total_courses,
        }


def _table_exists(conn, name: str) -> bool:
    r = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return r is not None


# ── Dept list ──────────────────────────────────────────────────

def get_edudrill_depts() -> list:
    """Return all dept cards with live student/attendance stats."""
    with _conn() as conn:
        result = []
        for dk, meta in DRILL_DEPT_META.items():
            # Count students by section (all sections in this dept)
            all_sections = []
            for ck, cv in meta["courses"].items():
                all_sections.extend(cv["secs"])
            all_sections = list(set(all_sections))

            total_students = sum(
                _student_count_for_section(conn, sec) for sec in all_sections
            )
            avg_att = 0.0
            if all_sections:
                pcts = [_attendance_pct_for_section(conn, sec) for sec in all_sections]
                avg_att = round(sum(pcts) / len(pcts), 1)

            today_present = sum(
                _today_present_for_section(conn, sec) for sec in all_sections
            )
            fac_count = _faculty_count_for_dept(conn, dk)
            result.append({
                "key":            dk,
                "name":           meta["name"],
                "emoji":          meta["emoji"],
                "color":          meta["color"],
                "accent":         meta["accent"],
                "total_students": total_students,
                "total_faculty":  fac_count,
                "total_courses":  len(meta["courses"]),
                "total_sections": len(all_sections),
                "avg_att":        avg_att,
                "present_today":  today_present,
                "absent_today":   max(0, total_students - today_present),
            })
        return result


# ── Single dept ────────────────────────────────────────────────

def get_edudrill_dept(dept_key: str) -> dict:
    """Dept-level drill: all courses + per-section stats."""
    meta = DRILL_DEPT_META.get(dept_key.upper())
    if not meta:
        return {}
    with _conn() as conn:
        courses = []
        for ck, cv in meta["courses"].items():
            sections = []
            for sec in cv["secs"]:
                total = _student_count_for_section(conn, sec)
                att   = _attendance_pct_for_section(conn, sec)
                pres  = _today_present_for_section(conn, sec)
                sections.append({
                    "section":        sec,
                    "total_students": total,
                    "att_pct":        att,
                    "present_today":  pres,
                    "absent_today":   max(0, total - pres),
                })
            total_c = sum(s["total_students"] for s in sections)
            avg_c   = round(sum(s["att_pct"] for s in sections) / max(len(sections),1), 1)
            courses.append({
                "key":            ck,
                "name":           cv["name"],
                "hod":            cv["hod"],
                "color":          meta["color"],
                "sections":       sections,
                "total_students": total_c,
                "avg_att":        avg_c,
            })

        fac_list = []
        if _table_exists(conn, "faculty"):
            fac_list = _faculty_for_dept(conn, dept_key.upper())

        all_secs = list({sec for cv in meta["courses"].values() for sec in cv["secs"]})
        dept_students = sum(_student_count_for_section(conn, s) for s in all_secs)
        dept_att      = round(sum(_attendance_pct_for_section(conn, s) for s in all_secs)
                               / max(len(all_secs),1), 1)
        dept_present  = sum(_today_present_for_section(conn, s) for s in all_secs)

        return {
            "key":            dept_key.upper(),
            "name":           meta["name"],
            "emoji":          meta["emoji"],
            "color":          meta["color"],
            "accent":         meta["accent"],
            "courses":        courses,
            "faculty":        fac_list,
            "total_students": dept_students,
            "total_faculty":  len(fac_list),
            "avg_att":        dept_att,
            "present_today":  dept_present,
            "absent_today":   max(0, dept_students - dept_present),
        }


# ── Section students + faculty ─────────────────────────────────

def get_edudrill_section_students(dept_key: str, course_key: str,
                                   section: str) -> dict:
    meta = DRILL_DEPT_META.get(dept_key.upper(), {})
    cm   = meta.get("courses", {}).get(course_key.upper(), {})
    with _conn() as conn:
        students = _all_students_for_section(conn, section)
        stats = {
            "total":   len(students),
            "good":    sum(1 for s in students if s["status"] == "good"),
            "warn":    sum(1 for s in students if s["status"] == "warn"),
            "poor":    sum(1 for s in students if s["status"] == "poor"),
            "avg_att": round(sum(s["att_pct"] for s in students)
                             / max(len(students),1), 1),
        }
    return {
        "dept_key":    dept_key.upper(),
        "course_key":  course_key.upper(),
        "course_name": cm.get("name", course_key),
        "dept_name":   meta.get("name", dept_key),
        "dept_color":  meta.get("color", "#4ecba8"),
        "section":     section,
        "students":    students,
        "stats":       stats,
    }


def get_edudrill_section_faculty(dept_key: str) -> list:
    with _conn() as conn:
        if not _table_exists(conn, "faculty"):
            return []
        return _faculty_for_dept(conn, dept_key.upper())


# ── Register routes ────────────────────────────────────────────

def register_edudrill_routes(app, get_current_user):
    """Mount EduDrill analytics API routes onto FastAPI app."""
    from fastapi import Depends, HTTPException

    @app.get("/api/edudrill/overview")
    def edudrill_overview(_: dict = Depends(get_current_user)):
        try:
            return get_edudrill_overview()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/edudrill/depts")
    def edudrill_depts(_: dict = Depends(get_current_user)):
        try:
            return get_edudrill_depts()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/edudrill/dept/{dept_key}")
    def edudrill_dept(dept_key: str, _: dict = Depends(get_current_user)):
        try:
            data = get_edudrill_dept(dept_key)
            if not data:
                raise HTTPException(status_code=404, detail=f"Dept {dept_key} not found")
            return data
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/edudrill/dept/{dept_key}/course/{course_key}/section/{section}/students")
    def edudrill_section_students(dept_key: str, course_key: str, section: str,
                                   _: dict = Depends(get_current_user)):
        try:
            return get_edudrill_section_students(dept_key, course_key, section)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/edudrill/dept/{dept_key}/faculty")
    def edudrill_dept_faculty(dept_key: str, _: dict = Depends(get_current_user)):
        try:
            return get_edudrill_section_faculty(dept_key)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    log.info("EduDrill Pro analytics routes registered.")
