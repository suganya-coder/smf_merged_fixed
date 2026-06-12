# =============================================================
# database_postgres.py  —  Smart Attendance System  v8.5 (SQLite)
#
# NOTE: Despite the filename, this uses SQLite (not PostgreSQL).
#       attendance_session.py imports THIS file as "db".
# =============================================================
import os, sqlite3, logging
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


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS students (
            student_id   TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            roll_number  TEXT UNIQUE,
            mobile       TEXT,
            section      TEXT,
            twin_of      TEXT,
            consent      INTEGER DEFAULT 1,
            enrolled_on  TEXT,
            active       INTEGER DEFAULT 1
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
        """)

        _safe_add_column(conn, "students",          "gender",           "TEXT DEFAULT ''")
        _safe_add_column(conn, "students",          "department",       "TEXT DEFAULT ''")
        _safe_add_column(conn, "students",          "email",            "TEXT")
        _safe_add_column(conn, "twin_analysis_log", "iris_score",       "REAL DEFAULT 0")
        _safe_add_column(conn, "twin_analysis_log", "skeleton_score",   "REAL DEFAULT 0")
        _safe_add_column(conn, "twin_analysis_log", "periocular_score", "REAL DEFAULT 0")
        _safe_add_column(conn, "twin_analysis_log", "geometry_score",   "REAL DEFAULT 0")
        _safe_add_column(conn, "twin_analysis_log", "final_confidence", "REAL DEFAULT 0")
        _safe_add_column(conn, "twin_analysis_log", "decision",         "TEXT")
        _safe_add_column(conn, "twin_analysis_log", "feature_vector",   "TEXT")
        # Password-based auth migration
        _safe_add_column(conn, "faculty", "password", "TEXT DEFAULT 'Staff@123'")
        conn.execute("""
            UPDATE faculty
            SET password='Staff@123'
            WHERE password IS NULL OR password=''
        """)
        _safe_add_column(conn, "hods", "password", "TEXT DEFAULT 'Hod@123'")
        conn.execute("""
            UPDATE hods
            SET password='Hod@123'
            WHERE password IS NULL OR password=''
        """)

        row = conn.execute("SELECT COUNT(*) FROM timetable").fetchone()[0]
        if row != len(config.DEFAULT_PERIODS):
            conn.execute("DELETE FROM timetable")
            for p in config.DEFAULT_PERIODS:
                conn.execute(
                    "INSERT INTO timetable(period_name,start_time,end_time) VALUES(?,?,?)",
                    (p["name"], p["start"], p["end"])
                )

    log.info("SQLite DB ready at %s", DB_PATH)
    print(f"  DB: {DB_PATH}")


def _safe_add_column(conn, table, column, definition):
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError:
        pass


def get_all_students():
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM students WHERE active=1 ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]


def get_student(student_id: str):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM students WHERE student_id=? AND active=1",
            (student_id,)
        ).fetchone()
        return dict(row) if row else None


def add_student(student_id, name, roll_number, section="",
                mobile="", mobile_number="", twin_of=None,
                consent=True, gender="", department="",
                email=None, face_encoding_path="",
                dataset_folder="") -> bool:
    mob = mobile_number or mobile
    try:
        with db() as conn:
            conn.execute("""
                INSERT INTO students
                    (student_id, name, roll_number, section, mobile,
                     twin_of, consent, enrolled_on, active)
                VALUES (?,?,?,?,?,?,?,?,1)
            """, (student_id, name, roll_number.lower(), section,
                  mob, twin_of, 1 if consent else 0,
                  datetime.now().strftime("%Y-%m-%d")))
            if gender:
                try:
                    conn.execute(
                        "UPDATE students SET gender=? WHERE student_id=?",
                        (gender, student_id)
                    )
                except Exception:
                    pass
        return True
    except sqlite3.IntegrityError:
        return False


def update_student_gender(student_id: str, gender: str):
    try:
        with db() as conn:
            conn.execute(
                "UPDATE students SET gender=? WHERE student_id=?",
                (gender, student_id)
            )
    except Exception:
        pass


def delete_student_data(student_id: str):
    with db() as conn:
        conn.execute(
            "UPDATE students SET active=0 WHERE student_id=?",
            (student_id,)
        )


def register_twin_pair(id1: str, id2: str):
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
                    (student_id, name, period, date, time, confidence, engine,
                     camera_id, liveness_score, twin_verified, skeleton_score)
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
    return get_attendance_by_date(datetime.now().strftime("%Y-%m-%d"), period)


def get_attendance_by_date(date_str: str, period: str = None):
    with db() as conn:
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
                    (student_id, name, period, date, time,
                     confidence, engine, camera_id)
                VALUES (?,?,?,?,?,1.0,'manual_override','TEACHER')
            """, (student_id, name, period, today, now))
        else:
            conn.execute(
                "DELETE FROM attendance WHERE student_id=? AND period=? AND date=?",
                (student_id, period, today)
            )
        conn.execute("""
            INSERT INTO override_log(student_id, period, action, note, teacher)
            VALUES (?,?,?,?,?)
        """, (student_id, period, action, note, teacher))


def get_override_log(limit: int = 200) -> list:
    with db() as conn:
        rows = conn.execute("""
            SELECT ol.*, s.name AS student_name, s.roll_number
            FROM override_log ol
            LEFT JOIN students s ON ol.student_id = s.student_id
            ORDER BY ol.created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_low_attendance_students(threshold: float = 75.0, days: int = 30) -> list:
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with db() as conn:
        rows = conn.execute("""
            SELECT s.student_id, s.name, s.roll_number, s.section,
                   s.department, s.student_email, s.parent_email,
                   COUNT(DISTINCT a.date) AS present_days,
                   ? AS total_days
            FROM students s
            LEFT JOIN attendance a
                ON s.student_id = a.student_id AND a.date >= ?
            WHERE s.active=1
            GROUP BY s.student_id
            HAVING CAST(present_days AS REAL) / ? * 100 < ?
            ORDER BY present_days ASC
        """, (days, cutoff, days, threshold)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            t = d.get("total_days") or 1
            p = d.get("present_days") or 0
            d["pct"] = round(p / t * 100, 1)
            result.append(d)
        return result


def get_attendance_summary(days: int = 30):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with db() as conn:
        rows = conn.execute("""
            SELECT s.student_id, s.name, s.roll_number,
                   COUNT(DISTINCT a.date) AS present_count,
                   ? AS total_days,
                   s.twin_of IS NOT NULL AS is_twin
            FROM students s
            LEFT JOIN attendance a
                ON s.student_id = a.student_id AND a.date >= ?
            WHERE s.active=1
            GROUP BY s.student_id
            ORDER BY s.name
        """, (days, cutoff)).fetchall()
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
                      feature_vector="", twin_partner_id=None):
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
        e = str(p.get("end",   ""))[:5]
        if s <= now <= e:
            return p.get("name", "")
    return None


def log_audit(user_name, action, resource="", detail="", ip_address=""):
    try:
        with db() as conn:
            conn.execute("""
                INSERT INTO audit_log
                    (user_name, action, resource, detail, ip_address)
                VALUES (?,?,?,?,?)
            """, (user_name, action, resource, detail, ip_address))
    except Exception as e:
        log.warning("audit_log failed (non-fatal): %s", e)


# Stub for get_faculty_by_id — needed by api.py
def get_faculty_by_id(fac_id: str):
    return None


# =============================================================
# STARTUP
# =============================================================
try:
    init_db()
except Exception as _e:
    log.error("DB init failed: %s", _e)
