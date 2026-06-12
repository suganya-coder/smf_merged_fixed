#!/usr/bin/env python3
"""
migrate_override.py — Smart Attendance System v10.0 Migration
==============================================================
Run once to:
  1. Create the attendance_overrides table
  2. Add class-incharge columns to the faculty table
  3. Migrate any existing override_log records into attendance_overrides

Usage:
    python migrate_override.py
    python migrate_override.py --db /path/to/attendance.db
"""
import os, sys, sqlite3, json, argparse
from datetime import datetime

def run_migration(db_path: str):
    print(f"\n Smart Attendance System — Override Feature Migration")
    print(f"  DB: {db_path}\n")

    if not os.path.exists(db_path):
        print(f"  ERROR: DB not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    def safe_add(table, col, definition):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            print(f"  + Added column {table}.{col}")
        except sqlite3.OperationalError:
            print(f"  · Column {table}.{col} already exists")

    # ── Step 1: Create attendance_overrides table ──────────────
    print("Step 1: Creating attendance_overrides table...")
    conn.executescript("""
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
    """)
    print("  ✓ attendance_overrides table ready")

    # ── Step 2: Extend faculty table ──────────────────────────
    print("\nStep 2: Extending faculty table with class-incharge fields...")
    # Check if faculty table exists
    tbl = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='faculty'").fetchone()
    if tbl:
        safe_add("faculty", "is_class_incharge",    "INTEGER DEFAULT 0")
        safe_add("faculty", "incharge_department",  "TEXT DEFAULT ''")
        safe_add("faculty", "incharge_year",        "TEXT DEFAULT ''")
        safe_add("faculty", "incharge_section",     "TEXT DEFAULT ''")
        safe_add("faculty", "role",                 "TEXT DEFAULT 'staff'")
        # back-fill from class_incharge_dept if present
        conn.execute("""
            UPDATE faculty
            SET is_class_incharge = 1,
                incharge_department = COALESCE(NULLIF(class_incharge_dept,''), incharge_department),
                incharge_year       = COALESCE(NULLIF(CAST(class_incharge_year AS TEXT),'0'), incharge_year),
                incharge_section    = COALESCE(NULLIF(class_incharge_section,''), incharge_section)
            WHERE class_incharge_dept != '' AND class_incharge_dept IS NOT NULL
        """)
        updated = conn.execute("SELECT changes()").fetchone()[0]
        if updated:
            print(f"  · Back-filled {updated} class-incharge rows from existing data")
    else:
        print("  · faculty table not found — will be created at first startup")

    # ── Step 3: Migrate existing override_log ─────────────────
    print("\nStep 3: Migrating existing override_log records...")
    ov_log_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='override_log'"
    ).fetchone()

    if not ov_log_exists:
        print("  · override_log table not found — nothing to migrate")
    else:
        existing_count = conn.execute("SELECT COUNT(*) FROM attendance_overrides").fetchone()[0]
        if existing_count > 0:
            print(f"  · attendance_overrides already has {existing_count} rows — skipping migration")
        else:
            rows = conn.execute("""
                SELECT ol.*, s.name AS student_name, s.register_number,
                       s.department, s.section, s.year
                FROM override_log ol
                LEFT JOIN students s ON s.student_id = ol.student_id
                ORDER BY ol.id
            """).fetchall()
            migrated = 0
            for r in rows:
                r = dict(r)
                note = r.get("note") or ""
                action = r.get("action") or ""
                att_from = "Absent" if action == "mark_present" else "Present"
                att_to   = "Present" if action == "mark_present" else "Absent"
                # parse date/time
                created = r.get("created_at") or datetime.now().isoformat()
                parts = created.split(" ") if " " in created else created.split("T")
                ov_date = parts[0] if parts else datetime.now().strftime("%Y-%m-%d")
                ov_time = parts[1][:8] if len(parts) > 1 else "00:00:00"
                # parse staff role from note
                staff_role = "Staff Override"
                if "[admin" in note.lower() or "[hod" in note.lower(): staff_role = "Admin/HOD"
                elif "incharge" in note.lower(): staff_role = "Class Incharge"

                try:
                    conn.execute("""
                        INSERT INTO attendance_overrides (
                            department, year, semester, section,
                            student_register_number, student_name,
                            course_code, course_name,
                            period, attendance_from, attendance_to,
                            reason, overridden_by, staff_id, staff_role,
                            override_date, override_time
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        r.get("department") or "",
                        str(r.get("year") or ""),
                        "", r.get("section") or "",
                        r.get("register_number") or r.get("student_id") or "",
                        r.get("student_name") or r.get("student_id") or "",
                        "", "", r.get("period") or "",
                        att_from, att_to,
                        note, r.get("teacher") or "", r.get("teacher") or "",
                        staff_role, ov_date, ov_time,
                    ))
                    migrated += 1
                except Exception as e:
                    print(f"  ! Row {r.get('id')} skipped: {e}")

            print(f"  ✓ Migrated {migrated} records from override_log")

    conn.commit()
    conn.close()

    print("\n Migration complete! ✓")
    print(" You can now restart the FastAPI server.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Override feature migration")
    parser.add_argument("--db", default=None, help="Path to attendance.db")
    args = parser.parse_args()

    if args.db:
        db_path = args.db
    else:
        # Auto-detect
        script_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(script_dir, "attendance.db")

    run_migration(db_path)
