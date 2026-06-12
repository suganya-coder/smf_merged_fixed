#!/usr/bin/env python3
"""
migrate_hod_to_hods.py  —  EduTrack Pro
========================================
One-time migration: merges the legacy `hod` table into the canonical `hods`
table, then drops `hod`.

Safe to re-run: skips rows that are already in `hods` (no duplicate inserts),
and does nothing if `hod` no longer exists.

Usage:
    python migrate_hod_to_hods.py [path/to/attendance.db]
"""

import sys
import sqlite3
import os
from datetime import datetime

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(__file__), "attendance.db"
)


def run():
    print(f"EduTrack Pro  —  HOD table merger")
    print(f"Database : {DB_PATH}\n")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ── Check legacy table exists ─────────────────────────────────────────
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='hod'")
    if not cur.fetchone():
        print("SKIP: 'hod' table does not exist — migration already done.")
        conn.close()
        return

    # ── Step 1: Ensure hods has all enrollment columns ───────────────────
    print("Step 1 — Ensuring 'hods' table has all required columns …")
    cur.execute("PRAGMA table_info(hods)")
    existing_cols = {row[1] for row in cur.fetchall()}

    needed = [
        ("employee_code", "TEXT DEFAULT ''"),
        ("first_name",    "TEXT DEFAULT ''"),
        ("last_name",     "TEXT DEFAULT ''"),
        ("gender",        "TEXT DEFAULT ''"),
        ("date_of_birth", "TEXT DEFAULT ''"),
        ("joining_date",  "TEXT DEFAULT ''"),
        ("status",        "TEXT DEFAULT 'Active'"),
        ("enrolled_on",   "TEXT DEFAULT ''"),
        ("role",          "TEXT DEFAULT 'HOD'"),
    ]
    for col, defn in needed:
        if col not in existing_cols:
            cur.execute(f"ALTER TABLE hods ADD COLUMN {col} {defn}")
            print(f"  Added column: {col}")
        else:
            print(f"  Column already present: {col}")

    conn.commit()

    # ── Step 2: Migrate rows ──────────────────────────────────────────────
    print("\nStep 2 — Migrating rows from 'hod' → 'hods' …")
    cur.execute("SELECT * FROM hod")
    hod_rows = cur.fetchall()

    inserted = 0
    updated  = 0

    for row in hod_rows:
        r = dict(row)
        hod_id    = r["hod_id"]
        full_name = f"{r.get('first_name','')} {r.get('last_name','')}".strip()

        cur.execute("SELECT hod_id FROM hods WHERE hod_id=?", (hod_id,))
        exists = cur.fetchone()

        if exists:
            # Enrich existing hods record with richer enrollment data.
            # We preserve the hods name/email if already set; only fill
            # blanks from hod to avoid overwriting admin-managed display data.
            cur.execute("""
                UPDATE hods SET
                    employee_code = COALESCE(NULLIF(employee_code,''), ?),
                    first_name    = COALESCE(NULLIF(first_name,''),    ?),
                    last_name     = COALESCE(NULLIF(last_name,''),     ?),
                    gender        = COALESCE(NULLIF(gender,''),        ?),
                    date_of_birth = COALESCE(NULLIF(date_of_birth,''), ?),
                    joining_date  = COALESCE(NULLIF(joining_date,''),  ?),
                    status        = COALESCE(NULLIF(status,''),        ?),
                    enrolled_on   = COALESCE(NULLIF(enrolled_on,''),   ?),
                    role          = COALESCE(NULLIF(role,''),          ?),
                    dept          = COALESCE(NULLIF(dept,''),          ?),
                    email         = COALESCE(NULLIF(email,''),         ?),
                    mobile        = COALESCE(NULLIF(mobile,''),        ?)
                WHERE hod_id = ?
            """, (
                r.get("employee_code",""),
                r.get("first_name",""),
                r.get("last_name",""),
                r.get("gender",""),
                r.get("date_of_birth",""),
                r.get("joining_date",""),
                r.get("status","Active"),
                r.get("enrolled_on",""),
                r.get("role","HOD"),
                r.get("department",""),
                r.get("email",""),
                r.get("mobile",""),
                hod_id
            ))
            print(f"  Updated  (already in hods): {hod_id}  ({full_name or hod_id})")
            updated += 1
        else:
            cur.execute("""
                INSERT INTO hods (
                    hod_id, name, dept, designation, email, mobile,
                    password, joined_on, active,
                    employee_code, first_name, last_name, gender,
                    date_of_birth, joining_date, status, enrolled_on, role
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                hod_id,
                full_name or r.get("first_name", hod_id),
                r.get("department",""),
                r.get("designation","Head of Department"),
                r.get("email",""),
                r.get("mobile",""),
                "hod@2025",
                r.get("joining_date",""),
                r.get("active",1),
                r.get("employee_code",""),
                r.get("first_name",""),
                r.get("last_name",""),
                r.get("gender",""),
                r.get("date_of_birth",""),
                r.get("joining_date",""),
                r.get("status","Active"),
                r.get("enrolled_on", datetime.now().strftime("%Y-%m-%d")),
                r.get("role","HOD"),
            ))
            print(f"  Inserted new: {hod_id}  ({full_name or hod_id})")
            inserted += 1

    conn.commit()
    print(f"\n  → Inserted: {inserted}  Updated: {updated}")

    # ── Step 3: Drop legacy table ─────────────────────────────────────────
    print("\nStep 3 — Dropping legacy 'hod' table …")
    cur.execute("DROP TABLE IF EXISTS hod")
    conn.commit()
    print("  Done.")

    # ── Step 4: Verify ────────────────────────────────────────────────────
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='hod'")
    if cur.fetchone():
        print("\nERROR: 'hod' table still exists — something went wrong.")
    else:
        print("\n  'hod' table successfully removed.")

    cur.execute("SELECT COUNT(*) FROM hods")
    total = cur.fetchone()[0]
    print(f"  'hods' table now has {total} record(s).\n")

    cur.execute("SELECT hod_id, name, dept, email, enrolled_on FROM hods ORDER BY hod_id")
    rows = cur.fetchall()
    print(f"  {'HOD_ID':<10} {'NAME':<25} {'DEPT':<8} {'EMAIL':<30} ENROLLED")
    print(f"  {'-'*10} {'-'*25} {'-'*8} {'-'*30} {'-'*10}")
    for r in rows:
        print(f"  {r[0]:<10} {r[1]:<25} {r[2]:<8} {r[3]:<30} {r[4]}")

    conn.close()
    print("\nMigration complete. ✓")


if __name__ == "__main__":
    run()
