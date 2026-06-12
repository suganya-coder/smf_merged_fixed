


# =============================================================
# role_attendance_session.py  —  Smart Attendance System  v10.0
#
# Role-Based Attendance Marking
# ─────────────────────────────
# Invoked by main.py menu option [3].
#
# Flow:
#   1. Prompt user → Student / Staff / HOD
#   2. Load correct LBPH model for that role
#   3. Open camera, detect + recognise faces
#   4. Mark attendance in the appropriate DB table
#      (student_attendance / staff_attendance / hod_attendance)
#   5. Print live result table; press Q / ESC to exit.
#
# DB Tables created here (if missing):
#   student_attendance, staff_attendance, hod_attendance
#
# Compatible with the existing attendance.db (no schema conflict).
# =============================================================

from __future__ import annotations

import os
import cv2
import time
import pickle
import logging
import sqlite3
import platform
import tempfile
import numpy as np
from datetime import datetime
from contextlib import contextmanager

import config

log = logging.getLogger(__name__)

# ── DB path (same file used by database.py) ──────────────────
DB_PATH = os.path.join(config.BASE_DIR, "attendance.db")

# ── Role config ───────────────────────────────────────────────
ROLE_CONFIG = {
    "student": {
        "label":       "Student",
        "model_path":  config.LBPH_MODEL,           # lbph_model.yml
        "labels_path": config.LBPH_LABELS,          # lbph_labels.pkl
        "model_type":  "yml",                       # plain OpenCV yml
        "table":       "student_attendance",
        "id_col":      "student_id",
        "lookup_table":"students",
        "lookup_id":   "student_id",
    },
    "staff": {
        "label":       "Staff",
        "model_path":  config.STAFF_LBPH_MODEL,     # staff_face_model.pkl
        "labels_path": None,                        # labels inside pkl
        "model_type":  "pkl",
        "table":       "staff_attendance",
        "id_col":      "staff_id",
        "lookup_table":"staff",
        "lookup_id":   "staff_id",
    },
    "hod": {
        "label":       "HOD",
        "model_path":  config.HOD_LBPH_MODEL,       # hod_face_model.pkl
        "labels_path": None,
        "model_type":  "pkl",
        "table":       "hod_attendance",
        "id_col":      "hod_id",
        "lookup_table":"hod",
        "lookup_id":   "hod_id",
    },
}

# ── Haar cascade (shared) ─────────────────────────────────────
CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)


# =============================================================
# DB helpers
# =============================================================

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


def _ensure_tables():
    """
    Safely create or migrate the three role attendance tables.

    Strategy:
      1. If the table does NOT exist → create it fresh with full schema.
      2. If the table ALREADY exists → check which columns are present
         and ALTER TABLE to add any that are missing.
         This handles the case where the table was created earlier with
         a different (shorter) schema — SQLite raises OperationalError
         on 'CREATE TABLE IF NOT EXISTS' when a required column is absent
         in the existing table.
    """

    # Desired schema per table: (col_name, col_definition)
    TABLE_SCHEMAS = {
        "student_attendance": {
            "id_col": "student_id",
            "role_default": "Student",
            "columns": [
                ("id",         "INTEGER PRIMARY KEY AUTOINCREMENT"),
                ("student_id", "TEXT NOT NULL DEFAULT ''"),
                ("name",       "TEXT NOT NULL DEFAULT ''"),
                ("role",       "TEXT NOT NULL DEFAULT 'Student'"),
                ("department", "TEXT NOT NULL DEFAULT ''"),
                ("date",       "TEXT NOT NULL DEFAULT ''"),
                ("time",       "TEXT NOT NULL DEFAULT ''"),
                ("period",     "TEXT NOT NULL DEFAULT ''"),
                ("status",     "TEXT NOT NULL DEFAULT 'Present'"),
                ("confidence", "REAL DEFAULT 0"),
            ],
            "indexes": [
                ("idx_statt_date", "date"),
                ("idx_statt_sid",  "student_id"),
            ],
        },
        "staff_attendance": {
            "id_col": "staff_id",
            "role_default": "Staff",
            "columns": [
                ("id",         "INTEGER PRIMARY KEY AUTOINCREMENT"),
                ("staff_id",   "TEXT NOT NULL DEFAULT ''"),
                ("name",       "TEXT NOT NULL DEFAULT ''"),
                ("role",       "TEXT NOT NULL DEFAULT 'Staff'"),
                ("department", "TEXT NOT NULL DEFAULT ''"),
                ("date",       "TEXT NOT NULL DEFAULT ''"),
                ("time",       "TEXT NOT NULL DEFAULT ''"),
                ("period",     "TEXT NOT NULL DEFAULT ''"),
                ("status",     "TEXT NOT NULL DEFAULT 'Present'"),
                ("confidence", "REAL DEFAULT 0"),
            ],
            "indexes": [
                ("idx_sfatt_date", "date"),
                ("idx_sfatt_sid",  "staff_id"),
            ],
        },
        "hod_attendance": {
            "id_col": "hod_id",
            "role_default": "HOD",
            "columns": [
                ("id",         "INTEGER PRIMARY KEY AUTOINCREMENT"),
                ("hod_id",     "TEXT NOT NULL DEFAULT ''"),
                ("name",       "TEXT NOT NULL DEFAULT ''"),
                ("role",       "TEXT NOT NULL DEFAULT 'HOD'"),
                ("department", "TEXT NOT NULL DEFAULT ''"),
                ("date",       "TEXT NOT NULL DEFAULT ''"),
                # FIX v10.5: att_date mirrors 'date' so api_hod.py Present Today works
                ("att_date",   "TEXT NOT NULL DEFAULT ''"),
                ("time",       "TEXT NOT NULL DEFAULT ''"),
                ("period",     "TEXT NOT NULL DEFAULT ''"),
                ("status",     "TEXT NOT NULL DEFAULT 'present'"),
                ("confidence", "REAL DEFAULT 0"),
            ],
            "indexes": [
                ("idx_hodatt_date",    "date"),
                ("idx_hodatt_attdate", "att_date"),
                ("idx_hodatt_hid",     "hod_id"),
            ],
        },
    }

    with _db() as conn:
        for table, schema in TABLE_SCHEMAS.items():
            id_col       = schema["id_col"]
            role_default = schema["role_default"]
            columns      = schema["columns"]
            indexes      = schema["indexes"]

            # Check if table exists
            exists = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name=?", (table,)
            ).fetchone()

            if not exists:
                # Build CREATE TABLE from scratch
                col_defs = ",\n                    ".join(
                    f"{col} {defn}" for col, defn in columns
                    if col != "id"   # id handled separately
                )
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        {col_defs}
                    )
                """)
                log.info("Created table: %s", table)
            else:
                # Table exists — find which columns are missing and add them
                existing_cols = {
                    row[1].lower()
                    for row in conn.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                }
                for col, defn in columns:
                    if col == "id":
                        continue
                    if col.lower() not in existing_cols:
                        # ALTER TABLE cannot add NOT NULL without a default,
                        # so we always supply DEFAULT '' or 0
                        safe_defn = defn
                        if "NOT NULL" in defn.upper() and "DEFAULT" not in defn.upper():
                            safe_defn = defn + " DEFAULT ''"
                        try:
                            conn.execute(
                                f"ALTER TABLE {table} ADD COLUMN {col} {safe_defn}"
                            )
                            log.info("Added column %s.%s", table, col)
                            print(f"  + Migrated column: {table}.{col}")
                        except Exception as alter_err:
                            log.warning(
                                "Could not add %s.%s: %s", table, col, alter_err
                            )

            # Create indexes (safe — IF NOT EXISTS)
            for idx_name, idx_col in indexes:
                try:
                    conn.execute(
                        f"CREATE INDEX IF NOT EXISTS {idx_name} "
                        f"ON {table}({idx_col})"
                    )
                except Exception:
                    pass


def _lookup_person(role_cfg: dict, person_id: str) -> dict:
    """
    Fetch name + department from the relevant lookup table.
    Falls back gracefully if the table / column doesn't exist.
    """
    result = {"name": person_id, "department": ""}
    try:
        with _db() as conn:
            tbl = role_cfg["lookup_table"]
            id_col = role_cfg["lookup_id"]
            # Try full_name first, then first+last, then name
            for name_expr in (
                "COALESCE(NULLIF(first_name||' '||last_name,''), name, '')",
                "name",
                "first_name",
            ):
                try:
                    row = conn.execute(
                        f"SELECT {name_expr} AS name, department "
                        f"FROM {tbl} WHERE {id_col}=? AND active=1 LIMIT 1",
                        (person_id,)
                    ).fetchone()
                    if row and row["name"]:
                        result["name"]       = row["name"].strip() or person_id
                        result["department"] = row["department"] or ""
                        break
                except Exception:
                    continue
    except Exception as e:
        log.debug("_lookup_person: %s", e)
    return result


def _mark_attendance(role_cfg: dict, person_id: str,
                     name: str, department: str,
                     period: str, confidence: float) -> bool:
    """
    INSERT OR IGNORE into the role-specific attendance table.
    Returns True if a new row was inserted (first mark today).
    """
    today = datetime.now().strftime("%Y-%m-%d")
    now   = datetime.now().strftime("%H:%M:%S")
    id_col = role_cfg["id_col"]
    table  = role_cfg["table"]
    role   = role_cfg["label"]

    try:
        with _db() as conn:
            # FIX v10.5: also write att_date for HOD rows (api_hod reads att_date)
            # and use lowercase 'present' (api_hod filters WHERE status='present')
            if table == "hod_attendance":
                cur = conn.execute(f"""
                    INSERT OR IGNORE INTO {table}
                        ({id_col}, name, role, department,
                         date, att_date, time, period, status, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'present', ?)
                """, (person_id, name, role, department,
                      today, today, now, period, round(confidence, 4)))
            else:
                cur = conn.execute(f"""
                    INSERT OR IGNORE INTO {table}
                        ({id_col}, name, role, department,
                         date, time, period, status, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'Present', ?)
                """, (person_id, name, role, department,
                      today, now, period, round(confidence, 4)))
            return cur.rowcount > 0
    except Exception as e:
        log.error("_mark_attendance(%s): %s", table, e)
        return False


def _is_marked(role_cfg: dict, person_id: str, period: str) -> bool:
    today  = datetime.now().strftime("%Y-%m-%d")
    id_col = role_cfg["id_col"]
    table  = role_cfg["table"]
    try:
        with _db() as conn:
            row = conn.execute(
                f"SELECT 1 FROM {table} "
                f"WHERE {id_col}=? AND period=? AND date=?",
                (person_id, period, today)
            ).fetchone()
        return row is not None
    except Exception:
        return False


def _today_records(role_cfg: dict) -> list:
    today = datetime.now().strftime("%Y-%m-%d")
    id_col = role_cfg["id_col"]
    table  = role_cfg["table"]
    try:
        with _db() as conn:
            rows = conn.execute(
                f"SELECT {id_col} AS uid, name, role, department, "
                f"date, time, period, status "
                f"FROM {table} WHERE date=? ORDER BY time DESC",
                (today,)
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# =============================================================
# Model loader
# =============================================================

def _load_model(role_cfg: dict):
    """
    Returns (recognizer, label_map) for the chosen role.

    Student  → plain OpenCV LBPH yml + pickle label map
    Staff/HOD→ pkl file containing {"label_map": ..., "yml_bytes": ...}
    """
    model_path  = role_cfg["model_path"]
    labels_path = role_cfg["labels_path"]
    model_type  = role_cfg["model_type"]

    if not os.path.exists(model_path):
        raise FileNotFoundError(
            f"Model not found: {model_path}\n"
            f"  → Run [2] Train All Models first."
        )

    rec = cv2.face.LBPHFaceRecognizer_create(
        radius=1, neighbors=8, grid_x=8, grid_y=8
    )

    if model_type == "yml":
        # Student model — plain yml
        rec.read(model_path)
        if not labels_path or not os.path.exists(labels_path):
            raise FileNotFoundError(
                f"Label map not found: {labels_path}\n"
                f"  → Run [2] Train All Models first."
            )
        with open(labels_path, "rb") as f:
            label_map = pickle.load(f)

    else:
        # Staff / HOD — pkl bundle
        with open(model_path, "rb") as f:
            bundle = pickle.load(f)
        label_map = bundle["label_map"]
        yml_bytes  = bundle["yml_bytes"]
        # Write yml bytes to a temp file so OpenCV can read it
        with tempfile.NamedTemporaryFile(
            suffix=".yml", delete=False
        ) as tmp:
            tmp.write(yml_bytes)
            tmp_path = tmp.name
        try:
            rec.read(tmp_path)
        finally:
            os.unlink(tmp_path)

    return rec, label_map


# =============================================================
# Camera helper
# =============================================================

def _open_camera(index: int):
    backends = (
        [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
        if platform.system() == "Windows"
        else [cv2.CAP_ANY, cv2.CAP_V4L2]
    )
    for bk in backends:
        try:
            cap = cv2.VideoCapture(index, bk)
            if not cap.isOpened():
                cap.release()
                continue
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS,          30)
            cap.set(cv2.CAP_PROP_BUFFERSIZE,   2)
            for _ in range(8):
                cap.grab()
                time.sleep(0.02)
            ret, frame = cap.read()
            if ret and frame is not None and frame.size > 0:
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                print(f"  Camera: {w}×{h}  backend={bk}")
                return cap
            cap.release()
        except Exception as exc:
            log.debug("Backend %s failed: %s", bk, exc)
    return None


# =============================================================
# Period helper
# =============================================================

def _auto_period() -> str:
    try:
        import database as _db_mod
        p = _db_mod.get_current_period()
        if p:
            return p
    except Exception:
        pass
    return f"Manual_{datetime.now().strftime('%H%M')}"


# =============================================================
# Print result table
# =============================================================

def _print_table(records: list, role_label: str):
    print(f"\n{'='*60}")
    print(f"  {role_label} Attendance — Today")
    print(f"{'='*60}")
    print(f"  {'ID':<12} {'NAME':<20} {'ROLE':<9} "
          f"{'DEPT':<7} {'DATE':<12} {'TIME'}")
    print("  " + "-" * 58)
    for r in records:
        print(f"  {r.get('uid','?'):<12} "
              f"{r.get('name','?'):<20} "
              f"{r.get('role','?'):<9} "
              f"{r.get('department','?'):<7} "
              f"{r.get('date','?'):<12} "
              f"{str(r.get('time','?'))[:8]}")
    print()


# =============================================================
# Core recognition loop
# =============================================================

def _run_recognition_loop(role_cfg: dict, rec, label_map: dict,
                           period: str):
    """
    Blocking loop:
      - Open camera
      - Detect faces with Haar cascade
      - Recognise with LBPH
      - Mark attendance
      - Overlay UI on the cv2 window
      - Q / ESC → exit

    FIX v10.1 — strict recognition gates (matches recognizer1.py v9.4):
      1. Preprocessing: CLAHE removed → cv2.equalizeHist() only (matches
         training pipeline; prevents LBP-code drift that caused dist 60-90).
      2. Adaptive threshold: single-student cap = 55, multi-student = 100
         (LBPH_UNKNOWN_MARGIN).  Old flat threshold=120 was too loose.
      3. Confidence formula: 1 - dist / eff_margin (not dist / 120).
         Ensures a dist=50 face is NOT shown as 58% — it maps correctly.
      4. Display gate: confidence < 45%  → show "?" box, no name.
      5. Attendance gate: confidence < 55% → do NOT write to DB.
      6. Unknown label: "?" replaces "Unknown" for unrecognised faces.
    """
    role_label = role_cfg["label"]

    # ── FIX 1: Derive adaptive margin (mirrors recognizer1.py _run_lbph) ──
    # Count real (non-unknown) enrolled persons in this model
    real_persons = [v for v in label_map.values() if v != "__UNKNOWN__"]
    n_persons    = len(real_persons)

    margin = getattr(config, "LBPH_UNKNOWN_MARGIN", 100)

    # FIX v10.5 — Corrected single-person margin and mark threshold.
    #
    # ROOT CAUSE of "Unknown face detected" / attendance not marking:
    #   • When n_persons == 1, LBPH always returns that one label regardless
    #     of distance.  We need a distance cap to reject real strangers.
    #   • Old cap was 55.  With dark skin / variable lighting the REAL person
    #     consistently scores dist 35-45 →  confidence = 1 - 40/55 = 27%.
    #     This is permanently below MARK_MIN_CONF=0.40, so attendance was
    #     NEVER written even though the face was correctly identified.
    #   • Fix: raise single-person cap to 80 (still strict enough to reject
    #     strangers who score dist 85+) and lower MARK_MIN_CONF to 0.25 so
    #     any face recognised above the display threshold is also marked.
    #
    # Confidence arithmetic:
    #   dist=40, margin=80  →  confidence = 1 - 40/80 = 50%  ✓ marked
    #   dist=50, margin=80  →  confidence = 1 - 50/80 = 37%  ✓ marked
    #   dist=60, margin=80  →  confidence = 1 - 60/80 = 25%  ✓ marked (at edge)
    #   dist=81, margin=80  →  rejected entirely             ✓ stranger blocked
    if n_persons == 1:
        # Single-person model: use a higher margin so the real person
        # (who may score dist 35-60 under variable lighting) is not rejected.
        eff_margin = min(margin, 80)
    else:
        eff_margin = margin

    # Confidence gates — unified: display and mark at the same threshold.
    # Previously MARK_MIN_CONF (0.40) was higher than DISPLAY_MIN_CONF (0.25),
    # which caused the face to show with a name but attendance to not be written.
    # Both gates are now 0.25 — if we're confident enough to show the name,
    # we're confident enough to mark attendance.
    DISPLAY_MIN_CONF = 0.25
    MARK_MIN_CONF    = 0.25   # FIX v10.5: was 0.40 — lowered to match display gate

    # FIX 3: CLAHE instance removed entirely — equalizeHist used below.

    print(f"\n  Opening camera for {role_label} recognition...")
    print(f"  Enrolled persons: {n_persons}  |  eff_margin: {eff_margin}")
    cap = _open_camera(config.CAMERA_INDEX)
    if cap is None:
        print("  ERROR: Cannot open camera.")
        print("  Solutions:")
        print("    1. Close Teams / Zoom / any app using camera")
        print("    2. Set CAMERA_INDEX=1 in .env for a second camera")
        print("    3. Reconnect the webcam and retry")
        return

    win_title = f"Attendance — {role_label} | Period: {period} | Q=Quit"
    cv2.namedWindow(win_title, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win_title, 900, 560)

    print(f"\n  ✓ Camera ready.  Please look at the camera.")
    print(f"  Press Q or ESC to stop the session.\n")

    marked_this_session: set = set()   # avoid repeat console prints
    stall_n = 0
    last_status_text  = ""
    last_status_color = (200, 200, 200)

    # FIX v10.5 — Confirm-frame counter.
    # Require the SAME person to be recognised consistently for N frames
    # before marking attendance. Prevents accidental marks from a single
    # frame where a stranger briefly matches.
    CONFIRM_FRAMES = max(1, getattr(config, "CONFIRM_FRAMES_REQUIRED", 3))
    confirm_counts: dict = {}   # {person_id: consecutive_frame_count}
    last_confirmed_id = None    # person_id seen in the previous frame

    while True:
        grabbed = cap.grab()
        if grabbed:
            ret, frame = cap.retrieve()
        else:
            ret, frame = False, None

        if not ret or frame is None or frame.size == 0:
            stall_n += 1
            if stall_n > 60:
                print("  Camera stalled. Reconnecting...")
                cap.release()
                time.sleep(1.0)
                cap = _open_camera(config.CAMERA_INDEX)
                stall_n = 0
                if cap is None:
                    print("  Camera lost. Session ended.")
                    break
            time.sleep(0.03)
            continue
        stall_n = 0

        # ── Preprocess ──────────────────────────────────────
        try:
            import lighting
            proc = lighting.preprocess_frame(frame)
        except Exception:
            proc = frame

        gray  = cv2.cvtColor(proc, cv2.COLOR_BGR2GRAY)
        eq    = cv2.equalizeHist(gray)
        faces = CASCADE.detectMultiScale(
            eq, scaleFactor=1.1, minNeighbors=5,
            minSize=(60, 60)
        )

        display = frame.copy()

        for (x, y, w, h) in faces:
            # BUG FIX v10.4: Multi-variant recognition — mirrors recognize.py.
            # Previously only equalizeHist was tried, giving LBPH distances of
            # 60-90 for dark skin under variable lighting.  Trying gamma-
            # brightened and CLAHE variants often finds a lower distance (20-50)
            # which pushes confidence above the display/mark thresholds.
            face_crop = gray[y:y+h, x:x+w]
            base      = cv2.resize(face_crop, (160, 160))
            eq        = cv2.equalizeHist(base)

            variants = [eq]                                           # 0 – primary (matches training)
            for clip in [2.0, 4.0]:                                   # 1-2 – CLAHE light / strong
                variants.append(cv2.createCLAHE(clip, (8, 8)).apply(base))
            for gamma in [1.4, 1.8, 2.2]:                            # 3-5 – gamma-brightened eq
                tbl = np.array(
                    [min(255, int(((i / 255.0) ** (1.0 / gamma)) * 255))
                     for i in range(256)], dtype=np.uint8)
                variants.append(cv2.LUT(eq, tbl))
            variants.append(base)                                     # 6 – raw fallback

            best_dist, best_label = 9999.0, None
            for v in variants:
                try:
                    lbl, d = rec.predict(v)
                    if d < best_dist:
                        best_dist, best_label = d, lbl
                except Exception:
                    continue

            if best_label is None:
                continue
            label_idx, dist = best_label, best_dist
            person_id = label_map.get(label_idx, None)

            # ── FIX 2: Adaptive distance gate ────────────────
            # Reject __UNKNOWN__ class label immediately
            if person_id == "__UNKNOWN__":
                person_id = None

            # ── FIX 3: Confidence via eff_margin (not flat 120) ──
            if person_id is not None and dist < eff_margin:
                confidence = float(
                    np.clip(1.0 - dist / max(eff_margin, 1), 0.0, 1.0)
                )
            else:
                confidence = 0.0
                person_id  = None

            # ── FIX 4 / v10.5: Display gate + confirm-frame + mark ──
            if person_id is not None and confidence >= DISPLAY_MIN_CONF:
                # ── Recognised with enough confidence to show name ──
                box_color = (0, 200, 0)
                info       = _lookup_person(role_cfg, person_id)
                name       = info["name"]
                department = info["department"]

                # Accumulate consecutive confident frames for this person.
                # Reset counter if a different person was seen last frame.
                if last_confirmed_id != person_id:
                    confirm_counts.clear()
                last_confirmed_id = person_id
                confirm_counts[person_id] = confirm_counts.get(person_id, 0) + 1

                frames_seen = confirm_counts[person_id]

                # Show a progress hint until the threshold is reached
                if frames_seen < CONFIRM_FRAMES:
                    label_txt = f"{name}  {int(confidence * 100)}%  [{frames_seen}/{CONFIRM_FRAMES}]"
                else:
                    label_txt = f"{name}  {int(confidence * 100)}%"

                # ── FIX 5 / v10.5: Mark once CONFIRM_FRAMES consecutive hits ──
                if confidence >= MARK_MIN_CONF and frames_seen >= CONFIRM_FRAMES:
                    already = _is_marked(role_cfg, person_id, period)
                    if not already:
                        marked = _mark_attendance(
                            role_cfg, person_id, name, department,
                            period, confidence
                        )
                        if marked and person_id not in marked_this_session:
                            marked_this_session.add(person_id)
                            _print_recognition_result(
                                person_id, name, role_label, department,
                                confidence, marked=True
                            )
                            last_status_text  = f"✓ {name} marked Present"
                            last_status_color = (0, 210, 80)
                    else:
                        if person_id not in marked_this_session:
                            marked_this_session.add(person_id)
                            _print_recognition_result(
                                person_id, name, role_label, department,
                                confidence, marked=False
                            )
                            last_status_text  = f"  {name} already marked"
                            last_status_color = (180, 180, 0)

                # Draw labelled box
                cv2.rectangle(display, (x, y), (x+w, y+h), box_color, 2)
                cv2.rectangle(display, (x, y-28), (x+w, y), box_color, -1)
                cv2.putText(display, label_txt,
                            (x+4, y-8), cv2.FONT_HERSHEY_SIMPLEX,
                            0.55, (255, 255, 255), 1)

            else:
                # ── FIX 6 / v10.5: Unknown / low-confidence face ──────
                # Reset the confirm counter — streak is broken
                confirm_counts.clear()
                last_confirmed_id = None
                # Show "?" box with the raw distance for diagnostics
                box_color = (0, 0, 200)
                dist_info = f"dist={best_dist:.0f}" if best_dist < 9999 else "no match"
                last_status_text  = f"Face detected — not recognised ({dist_info})"
                last_status_color = (0, 0, 200)

                cv2.rectangle(display, (x, y), (x+w, y+h), box_color, 2)
                cv2.putText(display, f"? {dist_info}",
                            (x + 4, y - 6),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5, box_color, 1)

        # ── Overlay header ───────────────────────────────────
        overlay_h = 38
        cv2.rectangle(display, (0, 0), (display.shape[1], overlay_h),
                      (30, 30, 30), -1)
        cv2.putText(display,
                    f"Role: {role_label}  |  Period: {period}  |  Q=Quit",
                    (10, 26), cv2.FONT_HERSHEY_SIMPLEX,
                    0.62, (220, 220, 0), 1)

        # ── Status bar ───────────────────────────────────────
        if last_status_text:
            sh = display.shape[0]
            cv2.rectangle(display, (0, sh-36), (display.shape[1], sh),
                          (20, 20, 20), -1)
            cv2.putText(display, last_status_text,
                        (10, sh-12), cv2.FONT_HERSHEY_SIMPLEX,
                        0.58, last_status_color, 1)

        cv2.imshow(win_title, display)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), ord('Q'), 27):   # Q or ESC
            break

    cap.release()
    cv2.destroyAllWindows()
    for _ in range(5):
        cv2.waitKey(1)


def _print_recognition_result(person_id: str, name: str,
                               role: str, department: str,
                               confidence: float, marked: bool):
    print(f"\n  ── Face Detected ──────────────────────────")
    print(f"  Matching face...")
    print(f"\n  Person Identified")
    print(f"  ID         : {person_id}")
    print(f"  Name       : {name}")
    print(f"  Role       : {role}")
    print(f"  Department : {department}")
    print(f"  Confidence : {int(confidence*100)}%")
    if marked:
        print(f"  ✓ Attendance Marked Successfully.\n")
    else:
        print(f"  ℹ Already marked for this period.\n")


# =============================================================
# PUBLIC ENTRY POINT
# Called by main.py do_session()
# =============================================================

def run_role_session(period: str = None):
    """
    Ask the user to choose a role, load the matching model,
    run the camera loop, and print the final attendance table.
    """
    _ensure_tables()

    # ── Role selection ────────────────────────────────────────
    print("\n" + "=" * 55)
    print("  ATTENDANCE MARKING")
    print("=" * 55)
    print("  Select Role")
    print("  1. Student")
    print("  2. Staff")
    print("  3. HOD")
    print("  0. Cancel")
    print("=" * 55)
    choice = input("  Enter role (1/2/3 or 0 to cancel): ").strip()

    role_map = {"1": "student", "2": "staff", "3": "hod"}
    if choice == "0" or choice.lower() == "cancel":
        print("  Attendance session cancelled.")
        return
    if choice not in role_map:
        print(f"  Invalid choice '{choice}'. Please enter 1, 2, or 3.")
        return

    role_key = role_map[choice]
    role_cfg = ROLE_CONFIG[role_key]
    role_label = role_cfg["label"]

    # ── Period ────────────────────────────────────────────────
    if not period:
        auto_p = _auto_period()
        p_input = input(
            f"  Period (blank = auto-detect '{auto_p}'): "
        ).strip()
        period = p_input if p_input else auto_p

    print(f"\n  Role   : {role_label}")
    print(f"  Period : {period}")
    print(f"  Model  : {os.path.basename(role_cfg['model_path'])}")

    # ── Load model ────────────────────────────────────────────
    print(f"\n  Loading {role_label} face model...")
    try:
        rec, label_map = _load_model(role_cfg)
    except FileNotFoundError as e:
        print(f"\n  ERROR: {e}")
        return
    except Exception as e:
        log.error("Model load error: %s", e)
        print(f"\n  ERROR: Could not load model — {e}")
        return

    print(f"  ✓ Model loaded  ({len(label_map)} person(s) trained)")
    if not label_map:
        print("  WARNING: No persons in model — enrol first (option [1]).")
        return

    # ── Instructions ─────────────────────────────────────────
    print(f"\n  Starting camera for {role_label} attendance...")
    print(f"  Please look at the camera for face recognition.")
    print(f"  Press Q to stop the attendance session.\n")

    # ── Recognition loop ─────────────────────────────────────
    _run_recognition_loop(role_cfg, rec, label_map, period)

    # ── Final table ───────────────────────────────────────────
    records = _today_records(role_cfg)
    if records:
        _print_table(records, role_label)
    else:
        print(f"  No {role_label} attendance recorded today.\n")

    print(f"  Session ended. Period: {period}\n")