# =============================================================
# database_extras.py  —  Smart Attendance System  v10.0
#
# New tables & helpers for:
#   - Semester / Course / Elective management
#   - Section-aware student enrollment (with email collection)
#   - Student & Staff Timetable
# =============================================================
import os, sqlite3, logging
from datetime import datetime, date, timedelta
from contextlib import contextmanager
import config

log = logging.getLogger(__name__)
DB_PATH = os.path.join(config.BASE_DIR, "attendance.db")


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


# =============================================================
# INIT — create all new tables
# =============================================================
def init_extras():
    with _db() as c:
        c.executescript("""
        -- ── Semesters ──────────────────────────────────────
        CREATE TABLE IF NOT EXISTS semesters (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            dept        TEXT NOT NULL,
            year        INTEGER NOT NULL,   -- 1,2,3,4
            sem_number  INTEGER NOT NULL,   -- 1..8
            start_date  TEXT,
            end_date    TEXT,
            is_current  INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        -- ── Course catalogue per dept/year/semester ──────
        CREATE TABLE IF NOT EXISTS courses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            dept        TEXT NOT NULL,
            year        INTEGER NOT NULL,
            semester    INTEGER NOT NULL,
            course_code TEXT NOT NULL,
            course_name TEXT NOT NULL,
            course_type TEXT DEFAULT 'core',   -- core | elective | lab
            credits     INTEGER DEFAULT 3,
            created_by  TEXT DEFAULT 'admin',
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(dept, semester, course_code)
        );

        -- ── Elective selections per student / section ────
        CREATE TABLE IF NOT EXISTS elective_selections (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id  TEXT,
            dept        TEXT NOT NULL,
            year        INTEGER NOT NULL,
            semester    INTEGER NOT NULL,
            section     TEXT NOT NULL DEFAULT 'A',
            course_code TEXT NOT NULL,
            selected_by TEXT DEFAULT 'admin',  -- who assigned it
            assigned_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(student_id, semester, course_code)
        );

        -- ── Students extended (year, dept, email, parent_email) ──
        CREATE TABLE IF NOT EXISTS student_extended (
            student_id     TEXT PRIMARY KEY,
            dept           TEXT NOT NULL DEFAULT '',
            year           INTEGER NOT NULL DEFAULT 1,
            section        TEXT NOT NULL DEFAULT 'A',
            semester       INTEGER NOT NULL DEFAULT 1,
            student_email  TEXT DEFAULT '',
            parent_email   TEXT DEFAULT '',
            staff_email    TEXT DEFAULT '',   -- class-incharge email
            hod_email      TEXT DEFAULT '',
            created_at     TEXT DEFAULT (datetime('now','localtime'))
        );

        -- ── Student Timetable ────────────────────────────
        CREATE TABLE IF NOT EXISTS student_timetable (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            dept        TEXT NOT NULL,
            year        INTEGER NOT NULL,
            semester    INTEGER NOT NULL DEFAULT 1,
            section     TEXT NOT NULL DEFAULT 'A',
            day_of_week TEXT NOT NULL,   -- MON,TUE,WED,THU,FRI,SAT
            period_no   INTEGER NOT NULL CHECK(period_no BETWEEN 1 AND 7),
            course_code TEXT NOT NULL,
            course_name TEXT NOT NULL,
            faculty_id  TEXT DEFAULT '',
            faculty_name TEXT DEFAULT '',
            room        TEXT DEFAULT '',
            UNIQUE(dept, year, semester, section, day_of_week, period_no)
        );

        -- ── Staff Timetable ──────────────────────────────
        CREATE TABLE IF NOT EXISTS staff_timetable (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            faculty_id  TEXT NOT NULL,
            day_of_week TEXT NOT NULL,
            period_no   INTEGER NOT NULL CHECK(period_no BETWEEN 1 AND 7),
            dept        TEXT NOT NULL DEFAULT '',
            year        INTEGER NOT NULL DEFAULT 1,
            section     TEXT NOT NULL DEFAULT 'A',
            semester    INTEGER NOT NULL DEFAULT 1,
            course_code TEXT NOT NULL,
            course_name TEXT NOT NULL,
            room        TEXT DEFAULT '',
            UNIQUE(faculty_id, day_of_week, period_no, semester)
        );

        CREATE INDEX IF NOT EXISTS idx_stt_dept  ON student_timetable(dept,year,semester,section);
        CREATE INDEX IF NOT EXISTS idx_ftt_fac   ON staff_timetable(faculty_id);
        CREATE INDEX IF NOT EXISTS idx_sem_dept  ON semesters(dept,is_current);
        CREATE INDEX IF NOT EXISTS idx_course_dept ON courses(dept,semester);
        """)

        # Safely extend students table with new columns
        for col, defn in [
            ("year",         "INTEGER DEFAULT 1"),
            ("student_email","TEXT DEFAULT ''"),
            ("parent_email", "TEXT DEFAULT ''"),
        ]:
            try:
                c.execute(f"ALTER TABLE students ADD COLUMN {col} {defn}")
            except sqlite3.OperationalError:
                pass

        # Migrate student_timetable: add semester column if missing
        try:
            c.execute("ALTER TABLE student_timetable ADD COLUMN semester INTEGER NOT NULL DEFAULT 1")
            log.info("Migrated student_timetable: added semester column")
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Migrate staff_timetable: add semester column if missing
        try:
            c.execute("ALTER TABLE staff_timetable ADD COLUMN semester INTEGER NOT NULL DEFAULT 1")
            log.info("Migrated staff_timetable: added semester column")
        except sqlite3.OperationalError:
            pass  # Column already exists

        _seed_courses(c)
        _seed_semesters(c)

    log.info("DB extras ready")


# =============================================================
# SEED DEFAULT COURSE CATALOGUE
# =============================================================
CORE_CURRICULUM = {
    # (dept, year, semester): [(code, name, type, credits), ...]
    ("CSE", 1, 1): [
        ("MA101","Engineering Mathematics I","core",4),
        ("PH101","Engineering Physics","core",3),
        ("CS101","Programming in C","core",3),
        ("CS102","Digital Logic Design","core",3),
        ("EN101","Technical English","core",2),
        ("CS111","C Programming Lab","lab",2),
    ],
    ("CSE", 1, 2): [
        ("MA102","Engineering Mathematics II","core",4),
        ("CS201","Data Structures","core",4),
        ("CS202","Object Oriented Programming","core",3),
        ("EC101","Basic Electronics","core",3),
        ("CS211","DS Lab","lab",2),
        ("CS212","OOP Lab","lab",2),
    ],
    ("CSE", 2, 3): [
        ("MA201","Discrete Mathematics","core",4),
        ("CS301","Design & Analysis of Algorithms","core",4),
        ("CS302","Database Management Systems","core",4),
        ("CS303","Computer Organization","core",3),
        ("CS311","DBMS Lab","lab",2),
        ("CS312","Algorithms Lab","lab",2),
    ],
    ("CSE", 2, 4): [
        ("CS401","Operating Systems","core",4),
        ("CS402","Computer Networks","core",4),
        ("CS403","Software Engineering","core",3),
        ("CS411","OS Lab","lab",2),
        ("CS412","Networks Lab","lab",2),
        ("CS413","Elective I","elective",3),  # placeholder
    ],
    ("CSE", 3, 5): [
        ("CS501","Machine Learning","core",4),
        ("CS502","Web Technologies","core",3),
        ("CS503","Compiler Design","core",3),
        ("CS511","ML Lab","lab",2),
        ("CS_E501","Elective II","elective",3),
        ("CS_E502","Elective III","elective",3),
    ],
    ("CSE", 3, 6): [
        ("CS601","Object Oriented Software Engineering","core",4),
        ("CS602","Embedded Systems and IoT","core",4),
        ("CS603","DevOps","core",4),
        ("CS604","Digital and Mobile Forensics","core",4),
        ("CS605","Web Technology","core",4),
        ("CS606","Introduction to Industrial Engineering","core",4),
        ("CS607","Video Creation and Editing","core",4),
        ("CS611","OOSE Lab","lab",2),
        ("CS612","IoT Lab","lab",2),
        ("CS613","Video Lab","lab",2),
        ("CS614","Web Technology Lab","lab",2),
        ("CS615","DevOps Lab","lab",2),
        ("CS616","Digital Forensics Lab","lab",2),
    ],
    ("CSE", 4, 7): [
        ("CS701","Deep Learning","core",4),
        ("CS702","Internet of Things","core",3),
        ("CS711","Project Phase I","lab",4),
        ("CS_E701","Elective VI","elective",3),
        ("CS_E702","Elective VII","elective",3),
    ],
    ("CSE", 4, 8): [
        ("CS801","Project Phase II","lab",6),
        ("CS802","Industry Internship","lab",4),
        ("CS_E801","Elective VIII","elective",3),
        ("CS_E802","Open Elective","elective",3),
    ],
}

# Elective pool — admin/HOD assigns these to elective slots
ELECTIVE_POOL = {
    "CSE": [
        ("CS_EL01","Artificial Intelligence","elective",3),
        ("CS_EL02","Big Data Analytics","elective",3),
        ("CS_EL03","Blockchain Technology","elective",3),
        ("CS_EL04","Natural Language Processing","elective",3),
        ("CS_EL05","Computer Vision","elective",3),
        ("CS_EL06","Augmented/Virtual Reality","elective",3),
        ("CS_EL07","Quantum Computing","elective",3),
        ("CS_EL08","DevOps & Containers","elective",3),
        ("CS_EL09","Data Mining","elective",3),
        ("CS_EL10","Edge Computing","elective",3),
    ],
    "AIDS": [
        ("AI_EL01","Reinforcement Learning","elective",3),
        ("AI_EL02","Explainable AI","elective",3),
        ("AI_EL03","Time Series Analysis","elective",3),
        ("AI_EL04","Graph Neural Networks","elective",3),
        ("AI_EL05","AutoML & MLOps","elective",3),
    ],
    "IT": [
        ("IT_EL01","Cloud Architecture","elective",3),
        ("IT_EL02","Network Security","elective",3),
        ("IT_EL03","IT Service Management","elective",3),
        ("IT_EL04","Digital Forensics","elective",3),
        ("IT_EL05","Microservices Architecture","elective",3),
    ],
    "CSBS": [
        ("CB_EL01","Business Intelligence","elective",3),
        ("CB_EL02","Financial Analytics","elective",3),
        ("CB_EL03","Supply Chain Analytics","elective",3),
        ("CB_EL04","HR Analytics","elective",3),
    ],
    "ECE": [
        ("EC_EL01","RF & Microwave Engineering","elective",3),
        ("EC_EL02","Advanced VLSI","elective",3),
        ("EC_EL03","Medical Electronics","elective",3),
        ("EC_EL04","Satellite Communication","elective",3),
        ("EC_EL05","Photonics","elective",3),
    ],
    "EEE": [
        ("EE_EL01","Renewable Energy Systems","elective",3),
        ("EE_EL02","Smart Grid Technology","elective",3),
        ("EE_EL03","FACTS & HVDC","elective",3),
        ("EE_EL04","Electric Vehicles","elective",3),
    ],
    "BM": [
        ("BM_EL01","Telemedicine & e-Health","elective",3),
        ("BM_EL02","Wearable Technology","elective",3),
        ("BM_EL03","Neural Engineering","elective",3),
        ("BM_EL04","Rehabilitation Engineering","elective",3),
    ],
    "MECH": [
        ("ME_EL01","Robotics & Automation","elective",3),
        ("ME_EL02","Additive Manufacturing","elective",3),
        ("ME_EL03","Finite Element Analysis","elective",3),
        ("ME_EL04","Tribology","elective",3),
        ("ME_EL05","Composite Materials","elective",3),
    ],
    "CIVIL": [
        ("CE_EL01","GIS & Remote Sensing","elective",3),
        ("CE_EL02","Environmental Engineering","elective",3),
        ("CE_EL03","Earthquake Engineering","elective",3),
        ("CE_EL04","Smart Infrastructure","elective",3),
    ],
}


def _seed_courses(conn):
    count = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
    if count > 0:
        return
    for (dept, year, sem), subjects in CORE_CURRICULUM.items():
        for code, name, ctype, cred in subjects:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO courses(dept,year,semester,course_code,course_name,course_type,credits) VALUES(?,?,?,?,?,?,?)",
                    (dept, year, sem, code, name, ctype, cred)
                )
            except Exception:
                pass
    # Seed all elective pools
    for dept, electives in ELECTIVE_POOL.items():
        for code, name, ctype, cred in electives:
            for sem in range(4, 9):  # electives available sem 4+
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO courses(dept,year,semester,course_code,course_name,course_type,credits) VALUES(?,?,?,?,?,?,?)",
                        (dept, (sem + 1) // 2, sem, code, name, ctype, cred)
                    )
                except Exception:
                    pass


def _seed_semesters(conn):
    count = conn.execute("SELECT COUNT(*) FROM semesters").fetchone()[0]
    if count > 0:
        return
    today = date.today()
    # Current semester = odd if Jun-Nov, even if Dec-May
    is_odd = today.month >= 6
    cur_sem = 1 if is_odd else 2
    start = date(today.year, 6, 1) if is_odd else date(today.year, 1, 1)
    end   = date(today.year, 11, 30) if is_odd else date(today.year, 5, 31)
    depts = ["CSE","AIDS","IT","CSBS","ECE","EEE","BM","MECH","CIVIL"]
    for dept in depts:
        for year in range(1, 5):
            sem = (year - 1) * 2 + cur_sem
            conn.execute(
                "INSERT OR IGNORE INTO semesters(dept,year,sem_number,start_date,end_date,is_current) VALUES(?,?,?,?,?,1)",
                (dept, year, sem, str(start), str(end))
            )


# =============================================================
# COURSE CRUD
# =============================================================
def get_courses(dept: str, semester: int = None, course_type: str = None) -> list:
    with _db() as c:
        sql = "SELECT * FROM courses WHERE dept=?"
        params = [dept]
        if semester:
            sql += " AND semester=?"; params.append(semester)
        if course_type:
            sql += " AND course_type=?"; params.append(course_type)
        sql += " ORDER BY semester, course_type DESC, course_name"
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def get_courses_by_year(dept: str, year: int) -> list:
    with _db() as c:
        rows = c.execute(
            "SELECT * FROM courses WHERE dept=? AND year=? ORDER BY semester, course_name",
            (dept, year)
        ).fetchall()
        return [dict(r) for r in rows]


def get_elective_pool(dept: str) -> list:
    with _db() as c:
        rows = c.execute(
            "SELECT * FROM courses WHERE dept=? AND course_type='elective' ORDER BY semester,course_name",
            (dept,)
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_course(dept, year, semester, course_code, course_name,
                  course_type="core", credits=3, created_by="admin") -> dict:
    with _db() as c:
        c.execute("""
            INSERT INTO courses(dept,year,semester,course_code,course_name,course_type,credits,created_by)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(dept,semester,course_code) DO UPDATE SET
                course_name=excluded.course_name,
                course_type=excluded.course_type,
                credits=excluded.credits
        """, (dept, year, semester, course_code, course_name, course_type, credits, created_by))
    return {"status": "ok", "course_code": course_code}


def assign_elective(student_id: str, dept: str, year: int, semester: int,
                    section: str, course_code: str, selected_by: str = "admin") -> dict:
    """Assign an elective to a student or a whole section."""
    with _db() as c:
        if student_id:
            c.execute("""
                INSERT INTO elective_selections(student_id,dept,year,semester,section,course_code,selected_by)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(student_id,semester,course_code) DO UPDATE SET selected_by=excluded.selected_by
            """, (student_id, dept, year, semester, section, course_code, selected_by))
        else:
            # Assign to whole section
            students = c.execute(
                "SELECT student_id FROM students WHERE active=1 AND section=? AND department=?",
                (section, dept)
            ).fetchall()
            for s in students:
                try:
                    c.execute("""
                        INSERT OR IGNORE INTO elective_selections
                        (student_id,dept,year,semester,section,course_code,selected_by)
                        VALUES(?,?,?,?,?,?,?)
                    """, (s["student_id"], dept, year, semester, section, course_code, selected_by))
                except Exception:
                    pass
    return {"status": "assigned"}


def get_elective_selections(dept: str, semester: int, section: str = None) -> list:
    with _db() as c:
        sql = """SELECT es.*, c.course_name, c.credits
                 FROM elective_selections es
                 JOIN courses c ON es.course_code=c.course_code AND es.dept=c.dept
                 WHERE es.dept=? AND es.semester=?"""
        params = [dept, semester]
        if section:
            sql += " AND es.section=?"; params.append(section)
        sql += " ORDER BY es.section, es.course_code"
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def get_current_semester(dept: str, year: int) -> dict:
    with _db() as c:
        row = c.execute(
            "SELECT * FROM semesters WHERE dept=? AND year=? AND is_current=1",
            (dept, year)
        ).fetchone()
        return dict(row) if row else {}


def get_all_semesters(dept: str) -> list:
    with _db() as c:
        rows = c.execute(
            "SELECT * FROM semesters WHERE dept=? ORDER BY year, sem_number",
            (dept,)
        ).fetchall()
        return [dict(r) for r in rows]


# =============================================================
# STUDENT EXTENDED (enrollment with email, year, section)
# =============================================================
def upsert_student_extended(student_id: str, dept: str, year: int,
                             section: str, semester: int,
                             student_email: str = "", parent_email: str = "",
                             staff_email: str = "", hod_email: str = "") -> dict:
    with _db() as c:
        c.execute("""
            INSERT INTO student_extended
                (student_id,dept,year,section,semester,student_email,parent_email,staff_email,hod_email)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(student_id) DO UPDATE SET
                dept=excluded.dept, year=excluded.year, section=excluded.section,
                semester=excluded.semester, student_email=excluded.student_email,
                parent_email=excluded.parent_email, staff_email=excluded.staff_email,
                hod_email=excluded.hod_email
        """, (student_id, dept, year, section, semester,
              student_email, parent_email, staff_email, hod_email))
    return {"status": "ok"}


def get_student_extended(student_id: str) -> dict:
    with _db() as c:
        row = c.execute(
            "SELECT * FROM student_extended WHERE student_id=?", (student_id,)
        ).fetchone()
        return dict(row) if row else {}


def get_section_students_extended(dept: str, year: int, section: str) -> list:
    with _db() as c:
        rows = c.execute("""
            SELECT s.*, se.year, se.section AS ext_section, se.semester,
                   se.student_email, se.parent_email, se.dept AS ext_dept
            FROM students s
            LEFT JOIN student_extended se ON s.student_id=se.student_id
            WHERE s.active=1 AND se.dept=? AND se.year=? AND se.section=?
            ORDER BY s.name
        """, (dept, year, section)).fetchall()
        return [dict(r) for r in rows]


# =============================================================
# TIMETABLE CRUD
# =============================================================
DAYS = ["MON","TUE","WED","THU","FRI","SAT"]
PERIOD_SLOTS = [
    {"no":1,"start":"08:55","end":"09:45","label":"Period 1  (08:55–09:45)"},
    {"no":2,"start":"09:45","end":"10:35","label":"Period 2  (09:45–10:35)"},
    {"no":3,"start":"10:55","end":"11:45","label":"Period 3  (10:55–11:45)  ← after Break"},
    {"no":4,"start":"11:45","end":"12:35","label":"Period 4  (11:45–12:35)"},
    {"no":5,"start":"13:35","end":"14:25","label":"Period 5  (13:35–14:25)  ← after Lunch"},
    {"no":6,"start":"14:25","end":"15:15","label":"Period 6  (14:25–15:15)"},
    {"no":7,"start":"15:15","end":"16:05","label":"Period 7  (15:15–16:05)"},
]


def upsert_student_timetable(dept: str, year: int, section: str,
                              day_of_week: str, period_no: int,
                              course_code: str, course_name: str,
                              faculty_id: str = "", faculty_name: str = "",
                              room: str = "", semester: int = 1) -> dict:
    with _db() as c:
        c.execute("""
            INSERT INTO student_timetable
                (dept,year,semester,section,day_of_week,period_no,course_code,course_name,faculty_id,faculty_name,room)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(dept,year,semester,section,day_of_week,period_no) DO UPDATE SET
                course_code=excluded.course_code, course_name=excluded.course_name,
                faculty_id=excluded.faculty_id, faculty_name=excluded.faculty_name,
                room=excluded.room
        """, (dept, year, semester, section, day_of_week.upper(), period_no,
              course_code, course_name, faculty_id, faculty_name, room))
    # Mirror to staff_timetable if faculty_id given
    if faculty_id:
        try:
            with _db() as c2:
                c2.execute("""
                    INSERT INTO staff_timetable
                        (faculty_id,day_of_week,period_no,dept,year,section,semester,course_code,course_name,room)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(faculty_id,day_of_week,period_no,semester) DO UPDATE SET
                        dept=excluded.dept,year=excluded.year,section=excluded.section,
                        semester=excluded.semester,
                        course_code=excluded.course_code,course_name=excluded.course_name,room=excluded.room
                """, (faculty_id, day_of_week.upper(), period_no,
                      dept, year, section, semester, course_code, course_name, room))
        except Exception:
            pass
    return {"status": "ok"}


def get_student_timetable(dept: str, year: int, section: str, semester: int = None) -> list:
    with _db() as c:
        if semester is not None:
            rows = c.execute("""
                SELECT * FROM student_timetable
                WHERE dept=? AND year=? AND semester=? AND section=?
                ORDER BY day_of_week, period_no
            """, (dept, year, semester, section)).fetchall()
        else:
            rows = c.execute("""
                SELECT * FROM student_timetable
                WHERE dept=? AND year=? AND section=?
                ORDER BY day_of_week, period_no
            """, (dept, year, section)).fetchall()
        return [dict(r) for r in rows]


def get_staff_timetable(faculty_id: str, dept: str = None, semester: int = None) -> list:
    with _db() as c:
        sql = "SELECT * FROM staff_timetable WHERE faculty_id=?"
        params = [faculty_id]
        if dept:
            sql += " AND dept=?"
            params.append(dept)
        if semester is not None:
            sql += " AND semester=?"
            params.append(semester)
        sql += " ORDER BY day_of_week, period_no"
        rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def delete_timetable_slot(dept: str, year: int, section: str,
                          day_of_week: str, period_no: int, semester: int = 1):
    with _db() as c:
        # find faculty first
        row = c.execute(
            "SELECT faculty_id FROM student_timetable WHERE dept=? AND year=? AND semester=? AND section=? AND day_of_week=? AND period_no=?",
            (dept, year, semester, section, day_of_week.upper(), period_no)
        ).fetchone()
        c.execute(
            "DELETE FROM student_timetable WHERE dept=? AND year=? AND semester=? AND section=? AND day_of_week=? AND period_no=?",
            (dept, year, semester, section, day_of_week.upper(), period_no)
        )
        if row and row["faculty_id"]:
            c.execute(
                "DELETE FROM staff_timetable WHERE faculty_id=? AND day_of_week=? AND period_no=?",
                (row["faculty_id"], day_of_week.upper(), period_no)
            )
    return {"status": "deleted"}


def get_period_slots() -> list:
    return PERIOD_SLOTS



# =============================================================
# SEED — D. VINOTH KUMAR + 3rd CSE-B TIMETABLE
# =============================================================
# 3rd CSE-B Timetable logic:
#   7 subjects × 4 theory classes = 28 theory slots
#   6 labs (OOSE, IoT, Video, Web, DevOps, DigitalForensics) × 3 (2-hr blocks = 2 consecutive periods)
#   Each lab occupies 2 consecutive periods on one day = 6 lab-pairs
#   Total slots used per week: 28 theory + 12 lab-period-slots = 40... spread across 6 days × 7 periods = 42 available
#   2 slots left free (used for association activities: SAT P6+P7)
#
# Vinoth Kumar also teaches:
#   III-MECH: AIML (course code AIML301) — via student_timetable for MECH
#   III-CSE A: Embedded System & IoT (CS602) — via student_timetable for CSE-A

_VINOTH_FAC_ID = "FAC_VK01"
_VINOTH_NAME   = "D. Vinoth Kumar"
_VINOTH_DEPT   = "AIDS"

# AIML course for MECH (add to MECH 3rd year catalogue on-the-fly)
_AIML_CODE = "AIML301"
_AIML_NAME = "Artificial Intelligence and Machine Learning"

# 3rd CSE-B subjects
_CSEB_SUBJECTS = [
    ("CS601", "Object Oriented Software Engineering"),
    ("CS602", "Embedded Systems and IoT"),
    ("CS603", "DevOps"),
    ("CS604", "Digital and Mobile Forensics"),
    ("CS605", "Web Technology"),
    ("CS606", "Introduction to Industrial Engineering"),
    ("CS607", "Video Creation and Editing"),
]

# Labs for 3rd CSE-B (code, name) — each gets 2 consecutive periods 3 times/week? 
# Requirement: 3 lab sessions per subject per week (each = 2 periods block)
# We have 6 lab subjects. 6×3 = 18 lab-period-pairs = 36 periods... too many.
# Re-reading requirement: "3 labs in a week" total (not per subject).
# So 3 lab sessions per week total, each = 2 consecutive periods.
# We allocate one lab (2 periods) per day on Tue/Thu/Fri (standard lab days).
# But 6 lab subjects × need coverage... realistically 1 lab slot per week rotates.
# We treat it as: each lab subject gets exactly 1 two-period block/week = 6 slots on different days.
# That matches "3 labs" if interpreted as 3 double-period lab sessions each week for IoT, Video, Web, DevOps, OOSE, DigForensics
# Practical interpretation: 6 lab pairs spread across Tue(2-hr), Thu(2-hr), Fri(2-hr), Mon-extra, Wed-extra, Sat(2-hr)
_CSEB_LABS = [
    ("CS611", "OOSE Lab"),
    ("CS612", "IoT Lab"),
    ("CS613", "Video Lab"),
    ("CS614", "Web Technology Lab"),
    ("CS615", "DevOps Lab"),
    ("CS616", "Digital Forensics Lab"),
]

# ── Full 3rd CSE-B Timetable ─────────────────────────────────────────────────
# Format: (day, period_no, course_code, course_name, faculty_id, faculty_name)
# Vinoth Kumar is faculty only for CS602 (E-IoT) slots in CSE-A, not CSE-B
# For CSE-B, different staff teach other subjects; Vinoth teaches ONLY E-IoT (CS602) for CSE-B too
# (since he is class incharge, we mark him as faculty for his subject in CSE-B)
# Other subjects: we assign placeholder faculty IDs from existing staff
# Each subject: 4 theory classes distributed across MON-SAT
# Labs: 6 lab subjects each get one double-period block

_CSEB_TT = [
    # ── MONDAY ──────────────────────────────────────────
    ("MON", 1, "CS606", "Introduction to Industrial Engineering",  "", ""),
    ("MON", 2, "CS604", "Digital and Mobile Forensics",             "", ""),
    ("MON", 3, "CS601", "Object Oriented Software Engineering",     "", ""),
    ("MON", 4, "CS605", "Web Technology",                           "", ""),
    ("MON", 5, "CS603", "DevOps",                                   "", ""),
    ("MON", 6, "CS611", "OOSE Lab",                                 "", ""),
    ("MON", 7, "CS611", "OOSE Lab",                                 "", ""),
    # ── TUESDAY ─────────────────────────────────────────
    ("TUE", 1, "CS607", "Video Creation and Editing",               "", ""),
    ("TUE", 2, "CS601", "Object Oriented Software Engineering",     "", ""),
    ("TUE", 3, "CS603", "DevOps",                                   "", ""),
    ("TUE", 4, "CS604", "Digital and Mobile Forensics",             "", ""),
    ("TUE", 5, "CS606", "Introduction to Industrial Engineering",   "", ""),
    ("TUE", 6, "CS612", "IoT Lab",                                  _VINOTH_FAC_ID, _VINOTH_NAME),
    ("TUE", 7, "CS612", "IoT Lab",                                  _VINOTH_FAC_ID, _VINOTH_NAME),
    # ── WEDNESDAY ───────────────────────────────────────
    ("WED", 1, "CS602", "Embedded Systems and IoT",                 _VINOTH_FAC_ID, _VINOTH_NAME),
    ("WED", 2, "CS605", "Web Technology",                           "", ""),
    ("WED", 3, "CS607", "Video Creation and Editing",               "", ""),
    ("WED", 4, "CS603", "DevOps",                                   "", ""),
    ("WED", 5, "CS606", "Introduction to Industrial Engineering",  "", ""),
    ("WED", 6, "CS613", "Video Lab",                                "", ""),
    ("WED", 7, "CS613", "Video Lab",                                "", ""),
    # ── THURSDAY ────────────────────────────────────────
    ("THU", 1, "CS604", "Digital and Mobile Forensics",             "", ""),
    ("THU", 2, "CS605", "Web Technology",                           "", ""),
    ("THU", 3, "CS606", "Introduction to Industrial Engineering",   "", ""),
    ("THU", 4, "CS607", "Video Creation and Editing",               "", ""),
    ("THU", 5, "CS602", "Embedded Systems and IoT",                 _VINOTH_FAC_ID, _VINOTH_NAME),
    ("THU", 6, "CS614", "Web Technology Lab",                       "", ""),
    ("THU", 7, "CS614", "Web Technology Lab",                       "", ""),
    # ── FRIDAY ──────────────────────────────────────────
    ("FRI", 1, "CS601", "Object Oriented Software Engineering",     "", ""),
    ("FRI", 2, "CS603", "DevOps",                                   "", ""),
    ("FRI", 3, "CS604", "Digital and Mobile Forensics",             "", ""),
    ("FRI", 4, "CS605", "Web Technology",                           "", ""),
    ("FRI", 5, "CS602", "Embedded Systems and IoT",                 _VINOTH_FAC_ID, _VINOTH_NAME),
    ("FRI", 6, "CS615", "DevOps Lab",                               "", ""),
    ("FRI", 7, "CS615", "DevOps Lab",                               "", ""),
    # ── SATURDAY ────────────────────────────────────────
    ("SAT", 1, "CS607", "Video Creation and Editing",               "", ""),
    ("SAT", 2, "CS602", "Embedded Systems and IoT",                 _VINOTH_FAC_ID, _VINOTH_NAME),  # Vinoth incharge association period
    ("SAT", 3, "CS616", "Digital Forensics Lab",                    "", ""),
    ("SAT", 4, "CS616", "Digital Forensics Lab",                    "", ""),
    ("SAT", 5, "CS601", "Object Oriented Software Engineering",     "", ""),
    ("SAT", 6, "CS999", "Association Activities",                   _VINOTH_FAC_ID, _VINOTH_NAME),
    ("SAT", 7, "CS999", "Association Activities",                   _VINOTH_FAC_ID, _VINOTH_NAME),
]

# Vinoth Kumar's own teaching timetable (staff_timetable)
# Teaching: III-MECH AIML + III-CSE-A Embedded IoT + III-CSE-B (incharge duties)
_VINOTH_STAFF_TT = [
    # day, period, dept, year, section, course_code, course_name
    ("MON", 3, "MECH", 3, "A", _AIML_CODE,  _AIML_NAME),
    ("MON", 6, "MECH", 3, "A", _AIML_CODE,  _AIML_NAME),
    ("TUE", 4, "CSE",  3, "A", "CS602", "Embedded Systems and IoT"),
    ("WED", 1, "MECH", 3, "A", _AIML_CODE,  _AIML_NAME),
    ("WED", 5, "CSE",  3, "A", "CS602", "Embedded Systems and IoT"),
    ("THU", 4, "MECH", 3, "A", _AIML_CODE,  _AIML_NAME),
    ("THU", 6, "CSE",  3, "A", "CS602", "Embedded Systems and IoT"),  # lab p1
    ("THU", 7, "CSE",  3, "A", "CS612", "IoT Lab"),                   # lab p2
    ("FRI", 1, "CSE",  3, "A", "CS602", "Embedded Systems and IoT"),
    ("FRI", 6, "MECH", 3, "A", _AIML_CODE,  _AIML_NAME),                 # AIML lab p1
    ("FRI", 7, "MECH", 3, "A", "AIML301L", "AIML Lab"),               # AIML lab p2
    ("SAT", 2, "CSE",  3, "A", "CS602", "Embedded Systems and IoT"),
    ("SAT", 6, "CSE",  3, "B", "CS999",  "Association Activities"),   # incharge
    ("SAT", 7, "CSE",  3, "B", "CS999",  "Association Activities"),   # incharge
]


def seed_vinoth_kumar() -> dict:
    """
    Idempotent seed:
      1. Ensures the faculty table exists (calls api_features._ensure_faculty_tables via sqlite directly).
      2. Inserts / updates D. Vinoth Kumar in the faculty table.
      3. Inserts / updates his staff_timetable rows.
      4. Inserts the full 3rd CSE-B student_timetable.
      5. Mirrors CSE-B E-IoT + AIML slots into III-CSE-A and III-MECH student_timetables.
    """
    import json
    results = {"faculty": None, "staff_tt_rows": 0, "cseb_tt_rows": 0,
               "csea_tt_rows": 0, "mech_tt_rows": 0}
    try:
        with _db() as c:
            # ── 1. Ensure faculty table columns exist ───────────────
            for col, defn in [
                ("class_incharge_dept",    "TEXT DEFAULT ''"),
                ("class_incharge_year",    "INTEGER DEFAULT 0"),
                ("class_incharge_section", "TEXT DEFAULT ''"),
            ]:
                try:
                    c.execute(f"ALTER TABLE faculty ADD COLUMN {col} {defn}")
                except Exception:
                    pass

            # ── 2. Insert / update Vinoth Kumar ────────────────────
            subjects_json = json.dumps([
                "Embedded Systems and IoT",
                "Artificial Intelligence and Machine Learning",
                "Association Activities",
            ])
            c.execute("""
                INSERT INTO faculty
                    (fac_id, name, dept, designation, email, mobile,
                     subjects, joined_on, active,
                     class_incharge_dept, class_incharge_year, class_incharge_section)
                VALUES (?,?,?,?,?,?,?,date('now'),1,?,?,?)
                ON CONFLICT(fac_id) DO UPDATE SET
                    name=excluded.name, dept=excluded.dept,
                    designation=excluded.designation,
                    email=excluded.email, mobile=excluded.mobile,
                    subjects=excluded.subjects, active=1,
                    class_incharge_dept=excluded.class_incharge_dept,
                    class_incharge_year=excluded.class_incharge_year,
                    class_incharge_section=excluded.class_incharge_section
            """, (
                _VINOTH_FAC_ID, _VINOTH_NAME, _VINOTH_DEPT,
                "Assistant Professor",
                "vinothkumar.ap@college.edu", "",
                subjects_json,
                "CSE", 3, "B"   # incharge of 3rd CSE-B
            ))
            results["faculty"] = "upserted"

            # ── 3. Vinoth Kumar staff_timetable ────────────────────
            for (day, pno, dept, yr, sec, ccode, cname) in _VINOTH_STAFF_TT:
                # Derive semester from year: odd year → sem 1/3/5/7, even → 2/4/6/8
                seed_sem = (yr * 2) - 1  # default to odd semester
                c.execute("""
                    INSERT INTO staff_timetable
                        (faculty_id, day_of_week, period_no, dept, year, section,
                         semester, course_code, course_name)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(faculty_id, day_of_week, period_no, semester) DO UPDATE SET
                        dept=excluded.dept, year=excluded.year,
                        section=excluded.section,
                        semester=excluded.semester,
                        course_code=excluded.course_code,
                        course_name=excluded.course_name
                """, (_VINOTH_FAC_ID, day, pno, dept, yr, sec, seed_sem, ccode, cname))
                results["staff_tt_rows"] += 1

            # ── 4. 3rd CSE-B student_timetable ────────────────────
            for (day, pno, ccode, cname, fid, fname) in _CSEB_TT:
                c.execute("""
                    INSERT INTO student_timetable
                        (dept, year, semester, section, day_of_week, period_no,
                         course_code, course_name, faculty_id, faculty_name)
                    VALUES (?,3,6,?,?,?,?,?,?,?)
                    ON CONFLICT(dept,year,semester,section,day_of_week,period_no) DO UPDATE SET
                        course_code=excluded.course_code,
                        course_name=excluded.course_name,
                        faculty_id=excluded.faculty_id,
                        faculty_name=excluded.faculty_name
                """, ("CSE", "B", day, pno, ccode, cname, fid, fname))
                results["cseb_tt_rows"] += 1

            # ── 5a. Mirror Vinoth's CSE-A E-IoT slots → III-CSE-A student_timetable ─
            csea_slots = [(d,p,cc,cn) for (d,p,dept,yr,sec,cc,cn) in _VINOTH_STAFF_TT
                         if dept=="CSE" and sec=="A" and yr==3]
            for (day, pno, ccode, cname) in csea_slots:
                c.execute("""
                    INSERT INTO student_timetable
                        (dept, year, semester, section, day_of_week, period_no,
                         course_code, course_name, faculty_id, faculty_name)
                    VALUES (?,3,6,?,?,?,?,?,?,?)
                    ON CONFLICT(dept,year,semester,section,day_of_week,period_no) DO UPDATE SET
                        course_code=excluded.course_code,
                        course_name=excluded.course_name,
                        faculty_id=excluded.faculty_id,
                        faculty_name=excluded.faculty_name
                """, ("CSE", "A", day, pno, ccode, cname,
                       _VINOTH_FAC_ID, _VINOTH_NAME))
                results["csea_tt_rows"] += 1

            # ── 5b. Mirror Vinoth's MECH AIML slots → III-MECH student_timetable ─────
            # First ensure AIML course exists in courses table for MECH
            try:
                c.execute("""
                    INSERT OR IGNORE INTO courses
                        (dept, year, semester, course_code, course_name,
                         course_type, credits, created_by)
                    VALUES (?,3,6,?,?,'core',4,'admin')
                """, ("MECH", _AIML_CODE, _AIML_NAME))
                c.execute("""
                    INSERT OR IGNORE INTO courses
                        (dept, year, semester, course_code, course_name,
                         course_type, credits, created_by)
                    VALUES (?,3,6,?,?,'lab',2,'admin')
                """, ("MECH", "AIML301L", "AIML Lab"))
            except Exception:
                pass

            mech_slots = [(d,p,cc,cn) for (d,p,dept,yr,sec,cc,cn) in _VINOTH_STAFF_TT
                         if dept=="MECH" and yr==3]
            for (day, pno, ccode, cname) in mech_slots:
                c.execute("""
                    INSERT INTO student_timetable
                        (dept, year, semester, section, day_of_week, period_no,
                         course_code, course_name, faculty_id, faculty_name)
                    VALUES (?,3,6,?,?,?,?,?,?,?)
                    ON CONFLICT(dept,year,semester,section,day_of_week,period_no) DO UPDATE SET
                        course_code=excluded.course_code,
                        course_name=excluded.course_name,
                        faculty_id=excluded.faculty_id,
                        faculty_name=excluded.faculty_name
                """, ("MECH", "A", day, pno, ccode, cname,
                       _VINOTH_FAC_ID, _VINOTH_NAME))
                results["mech_tt_rows"] += 1

        log.info("seed_vinoth_kumar complete: %s", results)
        return results
    except Exception as e:
        log.error("seed_vinoth_kumar failed: %s", e)
        return {"error": str(e)}

# =============================================================
# STARTUP
# =============================================================
try:
    init_extras()
    log.info("database_extras initialised")
except Exception as _e:
    log.error("database_extras init failed: %s", _e)

try:
    seed_vinoth_kumar()
    log.info("Vinoth Kumar seed complete")
except Exception as _e2:
    log.error("Vinoth Kumar seed failed: %s", _e2)
