



# =============================================================
# api_role_attendance.py  —  EduTrack Pro  v10.2
#
# Role-Based Attendance API Endpoints
# ─────────────────────────────────────
# These endpoints are intentionally public (no Bearer token)
# so the attendance kiosk can operate without login overhead.
#
# Endpoints registered into FastAPI app:
#
#   GET  /api/enrollment/counts              → {students, faculty, hods}
#   GET  /api/staff/by-dept?dept=CSE         → [{fac_id, name}, ...]
#   GET  /api/hod/by-dept?dept=CSE           → [{hod_id, name}, ...]
#
#   POST /api/role/session/start             → start role camera session
#   POST /api/role/session/stop              → stop session
#   GET  /api/role/session/status            → live status + marked list
#
# /video_feed  (already registered in api.py) serves the MJPEG stream.
# =============================================================

from __future__ import annotations

import os
import queue
import sqlite3
import logging
import threading
import datetime

# ── FIX: Import FastAPI / Pydantic at module level ─────────────────────────
# Previously these were imported inside register_role_attendance_routes().
# With `from __future__ import annotations` active, all annotations become
# ForwardRefs.  Pydantic v2's TypeAdapter cannot resolve ForwardRef('RoleSessionReq')
# when the class is defined inside a function scope — it is invisible at module
# level when FastAPI introspects the route for OpenAPI schema generation.
# Moving the import and the model class here (module level) solves the issue.
from fastapi import HTTPException, Depends
from pydantic import BaseModel

log = logging.getLogger(__name__)

# ── Shared role session state ──────────────────────────────────
_ROLE_SESSION: dict = {
    "running":        False,
    "role":           None,
    "period":         None,
    "dept":           None,
    "error":          None,
    "thread":         None,
    "started_at":     None,
    "last_detection": None,   # populated by _role_worker on every mark/duplicate
}
_ROLE_LOCK = threading.Lock()


# =============================================================
# FIX: Pydantic request model defined at MODULE level
# =============================================================
# Root cause of the PydanticUserError:
#   `from __future__ import annotations` turns every annotation into a
#   ForwardRef string (lazy evaluation).  When FastAPI generates /openapi.json,
#   Pydantic v2 tries to resolve ForwardRef('RoleSessionReq') for the TypeAdapter
#   it creates per-route.  Because the class was defined *inside*
#   register_role_attendance_routes(), it has no module-level binding, so
#   the forward reference can never be resolved → PydanticUserError.
#
# Fix:
#   1. Define RoleSessionReq here, at module scope.
#   2. Call .model_rebuild() so Pydantic v2 resolves all annotations eagerly
#      using the module's global namespace (where the class now lives).

class RoleSessionReq(BaseModel):
    role:     str = ""
    dept:     str = ""
    course:   str = ""
    year:     str = ""
    semester: str = ""
    section:  str = ""
    staff_id: str = ""
    hod_id:   str = ""
    period:   str = ""

# Eagerly rebuild so Pydantic v2 resolves all ForwardRefs now,
# using this module's globals — prevents TypeAdapter resolution failures.
RoleSessionReq.model_rebuild()


# =============================================================
# ROLE-AWARE HEADLESS WORKER
# Uses the same _FRAME_QUEUE / _SESSION_STATE as attendance_session.py
# so /video_feed works unchanged.
# =============================================================

def _role_worker(role: str, role_cfg: dict, period: str,
                 db_path: str, lbph_threshold: float):
    """
    Daemon thread that:
      1. Loads the role-specific LBPH model
      2. Borrows attendance_session._SESSION_STATE so /video_feed streams
      3. Runs headless face recognition
      4. Writes results to student_attendance / staff_attendance / hod_attendance
    """
    import cv2
    import pickle
    import numpy as np
    import attendance_session as _sess
    try:
        import liveness as _liveness_mod
        _liveness_available = True
    except ImportError:
        _liveness_mod = None
        _liveness_available = False

    global _ROLE_SESSION

    # ── mark the global session_state so /video_feed serves frames ──
    _sess._SESSION_STATE.update({
        "running":    True,
        "period":     period,
        "started_at": datetime.datetime.now().isoformat(),
        "error":      None,
        "thread":     None,
    })

    model_path  = role_cfg["model_path"]
    model_type  = role_cfg["model_type"]
    table       = role_cfg["table"]
    id_col      = role_cfg["id_col"]
    lookup_tbl  = role_cfg["lookup_table"]
    lookup_id   = role_cfg["lookup_id"]

    # ── Load model ─────────────────────────────────────────────
    try:
        if model_type == "yml":
            rec = cv2.face.LBPHFaceRecognizer_create()
            rec.read(model_path)
            with open(role_cfg["labels_path"], "rb") as f:
                label_map = pickle.load(f)            # {int_idx: person_id}
            if isinstance(label_map, dict):
                pass
            else:
                label_map = {i: v for i, v in enumerate(label_map)}
        else:  # pkl
            with open(model_path, "rb") as f:
                bundle = pickle.load(f)
            # ── BUG-1 FIX: pkl files store raw YAML bytes under 'yml_bytes', not a
            #    live recognizer object.  bundle.get("model") always returns None,
            #    causing rec.predict() to raise AttributeError silently swallowed
            #    by the except-continue block → attendance never marks.
            #    Reconstruct the LBPHFaceRecognizer by writing yml_bytes to a temp
            #    file and calling rec.read(), which is the only supported load path.
            if "yml_bytes" in bundle:
                import tempfile
                rec = cv2.face.LBPHFaceRecognizer_create()
                tmp_path = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".yml", delete=False) as tf:
                        tf.write(bundle["yml_bytes"])
                        tmp_path = tf.name
                    rec.read(tmp_path)
                    log.info("[ROLE-WORKER] PKL→YML reconstruct OK for role=%s (%d bytes)",
                             role, len(bundle["yml_bytes"]))
                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.unlink(tmp_path)
                        except Exception:
                            pass
                label_map = bundle.get("label_map") or bundle.get("labels") or {}
            else:
                rec = bundle.get("model") or bundle.get("recognizer")
                label_map = bundle.get("labels") or bundle.get("label_map") or {}
                if rec is None:
                    raise ValueError(
                        f"PKL bundle for role={role} has no 'yml_bytes', 'model', or 'recognizer' key. "
                        f"Keys found: {list(bundle.keys())}. Re-train this model."
                    )
    except Exception as exc:
        err = f"Model load failed for {role}: {exc}"
        log.error(err)
        _ROLE_SESSION["error"]   = err
        _ROLE_SESSION["running"] = False
        _sess._SESSION_STATE["running"] = False
        _sess._SESSION_STATE["error"]   = err
        return

    if not label_map:
        err = f"No persons enrolled for {role}. Run training first."
        _ROLE_SESSION["error"]   = err
        _ROLE_SESSION["running"] = False
        _sess._SESSION_STATE["running"] = False
        _sess._SESSION_STATE["error"]   = err
        return

    # ── BUG-5 FIX: Validate student label integrity ─────────────
    # Student PKL label maps built from legacy enrollment may contain HOD IDs
    # (e.g. "2299", "HOD001") instead of proper STU_* student IDs.
    # Recognising a face against these labels writes garbage IDs to the DB and
    # the real students (STU_23CS086 etc.) are never matched.
    # Detect this early and surface a clear warning rather than silently misfiring.
    _model_warning_msg = None
    if role == "student":
        _invalid_labels = [
            v for v in label_map.values()
            if v != "__UNKNOWN__" and not str(v).upper().startswith("STU_")
        ]
        _valid_labels = [
            v for v in label_map.values()
            if str(v).upper().startswith("STU_")
        ]
        if _invalid_labels:
            _model_warning_msg = (
                f"Student model contains {len(_invalid_labels)} invalid label(s) "
                f"that do not start with 'STU_': {_invalid_labels[:5]}. "
                f"Re-enroll real students (Main Menu → Enroll → Student) and retrain."
            )
            log.warning("[ROLE-WORKER] MODEL INTEGRITY WARNING: %s", _model_warning_msg)
            _ROLE_SESSION["model_warning"] = _model_warning_msg
        if not _valid_labels:
            err = (
                "Student model has NO valid STU_* labels — all enrolled IDs are invalid. "
                "Re-enroll students and retrain before starting a session."
            )
            log.error("[ROLE-WORKER] %s", err)
            _ROLE_SESSION["error"]   = err
            _ROLE_SESSION["running"] = False
            _sess._SESSION_STATE["running"] = False
            _sess._SESSION_STATE["error"]   = err
            return
        log.info("[ROLE-WORKER] Student model: %d valid STU_* labels, %d invalid skipped.",
                 len(_valid_labels), len(_invalid_labels))

    # ── Haar cascade ────────────────────────────────────────────
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    # FIX v10.3: CLAHE removed entirely — equalizeHist used in the loop below.
    # CLAHE preprocessing diverges from training → inflated LBPH distances.

    # ── Threshold logic v10.6 fix ────────────────────────────────
    # ROOT CAUSE of "28% shown but never marked":
    #
    #   The camera was correctly computing conf = 1 - dist/margin = 28%
    #   (dist ≈ 72, margin = 100).  But two separate bugs blocked marking:
    #
    #   BUG A — The 55-cap for single-enrolled-person:
    #     _eff_margin = min(100, 55) = 55 when n_persons == 1
    #     conf = 1 - 72/55 = -30%  →  negative → person_id set to None
    #     The face was REJECTED before even reaching the display/mark gates.
    #
    #   BUG B — _MARK_MIN = 0.40 was too high:
    #     Even without the 55-cap (multi-person case), 28% < 40% → blocked.
    #
    # FIX:
    #   1. Remove the 55-cap entirely.  LBPH naturally returns high distances
    #      (60-90) for correctly matched faces under real lighting; capping the
    #      margin to 55 just makes all those distances negative/rejected.
    #      The real stranger-rejection already happens via the margin gate
    #      (dist >= _eff_margin → person_id = None).
    #
    #   2. Lower _MARK_MIN to 0.20 (20%).  This means a face at dist < 80
    #      (out of margin 100) gets marked.  Faces with dist ≥ 80 (conf < 20%)
    #      are not marked — genuine strangers typically land at dist 90-120.
    #
    #   3. Lower _DISPLAY_MIN to 0.15 so the name appears on screen even while
    #      confidence is building up across frames.
    import config as _cfg
    _liveness_on        = getattr(_cfg, "LIVENESS_ON", False)
    _liveness_threshold = getattr(_cfg, "LIVENESS_THRESHOLD", 0.28)
    if not _liveness_available and _liveness_on:
        log.warning("[ROLE-WORKER] LIVENESS_ON=true but liveness.py not found — running WITHOUT anti-spoofing")
        _ROLE_SESSION["model_warning"] = "Liveness module missing — anti-spoofing disabled"
    _real_persons = [v for v in label_map.values() if v != "__UNKNOWN__"]
    _n_persons    = len(_real_persons)
    _margin       = getattr(_cfg, "LBPH_UNKNOWN_MARGIN", 100)
    # FIX: use full margin regardless of n_persons — the 55-cap caused
    # correctly-matched faces to compute negative confidence and be dropped.
    _eff_margin   = _margin

    _DISPLAY_MIN  = 0.35   # show name on screen if conf >= 35%
    _MARK_MIN     = 0.50   # write attendance only if conf >= 50%

    log.info("[ROLE-WORKER] n_persons=%d eff_margin=%d display_min=%.0f%% mark_min=%.0f%%",
             _n_persons, _eff_margin, _DISPLAY_MIN * 100, _MARK_MIN * 100)

    # ── Camera ──────────────────────────────────────────────────
    import config
    cam_idx = getattr(config, "CAMERA_INDEX", 0)
    cap = None
    for idx in [cam_idx, 0, 1, 2]:
        c = cv2.VideoCapture(idx)
        if c.isOpened():
            cap = c
            break
    if cap is None:
        err = "Cannot open camera. Close Teams/Zoom and retry."
        _ROLE_SESSION["error"]   = err
        _ROLE_SESSION["running"] = False
        _sess._SESSION_STATE["running"] = False
        _sess._SESSION_STATE["error"]   = err
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)

    marked_session: set = set()
    _confirm_counts: dict = {}  # person_id → consecutive frame count above _MARK_MIN
    _CONFIRM_FRAMES_NEEDED = 3  # must appear in 3 consecutive frames before marking
    _lookup_cache: dict = {}  # person_id → {name, department, section, ...}
    stall_n = 0
    role_label = role_cfg["label"]

    log.info("[ROLE-WORKER] Camera open, role=%s period=%s", role, period)

    def _db():
        conn = sqlite3.connect(db_path, timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _lookup(person_id: str):
        # Cache-first: student/staff profile never changes mid-session
        if person_id in _lookup_cache:
            return _lookup_cache[person_id]
        try:
            conn = _db()
            # For students, fetch extended fields needed for cross-section validation.
            # For staff and hod, use the original COALESCE query (those tables may not
            # have section/course/year columns and don't need the check).
            if role == "student":
                row = conn.execute(
                    "SELECT name, "
                    "COALESCE(NULLIF(department,''),'') AS department, "
                    "COALESCE(section,'') AS section, "
                    "COALESCE(course,'')  AS course, "
                    "COALESCE(year,'')    AS year, "
                    "active "
                    "FROM students WHERE student_id=?",
                    (person_id,)
                ).fetchone()
            else:
                # BUG-3 FIX: faculty table uses column "dept", hods table uses "dept".
                # Neither has a "department" column (or it is always empty).
                # COALESCE(NULLIF(dept,''), NULLIF(department,''), '') handles both tables
                # and also gracefully handles tables that have only one of the two columns.
                row = conn.execute(
                    f"SELECT name, COALESCE(NULLIF(dept,''), "
                    f"NULLIF(department,''), '') AS department "
                    f"FROM {lookup_tbl} WHERE {lookup_id}=?",
                    (person_id,)
                ).fetchone()
            conn.close()
            result = dict(row) if row else {"name": person_id, "department": "—"}
            if row:
                log.debug("[ROLE-WORKER] _lookup %s → name=%s dept=%s",
                          person_id, result.get("name"), result.get("department"))
            else:
                log.warning("[ROLE-WORKER] _lookup: no row for %s in %s.%s=%s",
                            person_id, lookup_tbl, lookup_id, person_id)
            _lookup_cache[person_id] = result  # cache for this session
            return result
        except Exception as exc:
            log.warning("[ROLE-WORKER] _lookup error for %s: %s", person_id, exc)
            fallback = {"name": person_id, "department": "—"}
            _lookup_cache[person_id] = fallback  # cache fallback to avoid repeat errors
            return fallback

    def _is_marked(person_id: str) -> bool:
        # Check in-memory cache first — avoids SQLite query every frame
        if person_id in marked_session:
            return True
        try:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            conn  = _db()
            row   = conn.execute(
                f"SELECT id FROM {table} WHERE {id_col}=? AND period=? AND date=?",
                (person_id, period, today)
            ).fetchone()
            conn.close()
            if row:
                marked_session.add(person_id)  # cache it so next check is instant
                return True
            return False
        except Exception:
            return False

    def _mark(person_id: str, name: str, dept: str, conf: float):
        try:
            today      = datetime.datetime.now().strftime("%Y-%m-%d")
            now_time   = datetime.datetime.now().strftime("%H:%M:%S")
            # Read session context for extra columns
            _section  = _ROLE_SESSION.get("section",  "") or ""
            _course   = _ROLE_SESSION.get("course",   "") or ""
            _year     = _ROLE_SESSION.get("year",     "") or ""
            _semester = _ROLE_SESSION.get("semester", "") or ""
            conn      = _db()
            if role == "student":
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} "
                    f"({id_col}, name, role, department, course, year, section, "
                    f"semester, date, time, period, status, confidence) "
                    f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (person_id, name, role_label, dept,
                     _course, _year, _section, _semester,
                     today, now_time, period, "Present", conf)
                )
            else:
                # Staff and HOD tables do not have course/year/section columns
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} "
                    f"({id_col}, name, role, department, date, time, period, status, confidence) "
                    f"VALUES (?,?,?,?,?,?,?,?,?)",
                    (person_id, name, role_label, dept,
                     today, now_time, period, "Present", conf)
                )
            conn.commit()
            conn.close()
            marked_session.add(person_id)  # update cache so _is_marked() is instant from now on
            log.info("[ROLE-WORKER] Marked %s (%s) Present | section=%s course=%s year=%s",
                     person_id, name, _section, _course, _year)
            # ── Update last_detection so frontend "Last Detection" card shows result ──
            _ROLE_SESSION["last_detection"] = {
                "status":     "success",
                "person_id":  person_id,
                "name":       name,
                "department": dept,
                "role":       role_label,
                "confidence": round(conf, 4),
                "time":       now_time,
                "section":    _section,
                "course":     _course,
                "year":       _year,
            }
        except Exception as exc:
            log.warning("[ROLE-WORKER] DB write error: %s", exc)

    # ── Main recognition loop ──────────────────────────────────
    # BUG-4 FIX: removed `and _sess._SESSION_STATE.get("running")` from the
    # condition.  _SESSION_STATE["running"] is shared with the legacy attendance
    # pipeline; any call to attendance_session.stop_session() (or a stale state
    # from a previous session) would silently kill this thread, leaving the
    # frontend frozen on "Scanning…" forever.  The role worker manages its own
    # lifecycle exclusively through _ROLE_SESSION["running"].
    # Push a warmup frame so /video_feed never times out during camera init
    _push_warmup_frame_to_queue()

    log.info("[ROLE-WORKER] Entering recognition loop. role=%s period=%s", role, period)
    while _ROLE_SESSION.get("running"):
        grabbed = cap.grab()
        ret, frame = (cap.retrieve() if grabbed else (False, None))

        if not ret or frame is None:
            stall_n += 1
            if stall_n > 80:
                cap.release()
                import time; time.sleep(1.0)
                cap = cv2.VideoCapture(cam_idx)
                for _ in range(5):          # flush stale buffered frames after reconnect
                    cap.grab()
                stall_n = 0
            import time; time.sleep(0.03)
            continue
        stall_n = 0

        display = frame.copy()
        gray    = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        eq      = cv2.equalizeHist(gray)
        faces   = cascade.detectMultiScale(eq, scaleFactor=1.1,
                                           minNeighbors=5, minSize=(60, 60))

        for (x, y, w, h) in faces:
            # BUG FIX v10.4: Multi-variant recognition — mirrors recognize.py.
            # Previously only equalizeHist was tried; dark skin under variable
            # lighting gives distances of 60-90 with that alone, causing the
            # confidence check to fail (conf 10-40% < old 45% floor → "?").
            # Trying gamma-brightened and CLAHE variants finds lower distances.
            _base = cv2.resize(gray[y:y+h, x:x+w], (160, 160))
            _eq   = cv2.equalizeHist(_base)
            _variants = [_eq]
            for _clip in [2.0, 4.0]:
                _variants.append(cv2.createCLAHE(_clip, (8, 8)).apply(_base))
            for _gamma in [1.4, 1.8, 2.2]:
                _tbl = np.array(
                    [min(255, int(((i / 255.0) ** (1.0 / _gamma)) * 255))
                     for i in range(256)], dtype=np.uint8)
                _variants.append(cv2.LUT(_eq, _tbl))
            _variants.append(_base)

            _best_dist, _best_lbl = 9999.0, None
            for _v in _variants:
                try:
                    _lbl, _d = rec.predict(_v)
                    if _d < _best_dist:
                        _best_dist, _best_lbl = _d, _lbl
                except Exception as _pred_exc:
                    log.debug("[ROLE-WORKER] predict() variant error (role=%s): %s",
                              role, _pred_exc)
                    continue

            if _best_lbl is None:
                continue
            label_idx, dist    = _best_lbl, _best_dist
            person_id = label_map.get(label_idx, None)

            # FIX: reject __UNKNOWN__ class label immediately
            if person_id == "__UNKNOWN__":
                person_id = None

            # FIX: compute confidence against eff_margin (not flat threshold=120)
            if person_id is not None and dist < _eff_margin:
                conf = float(np.clip(1.0 - dist / max(_eff_margin, 1), 0.0, 1.0))
            else:
                conf      = 0.0
                person_id = None

            # FIX: display gate — 45% minimum before showing name
            if person_id is not None and conf >= _DISPLAY_MIN:
                info  = _lookup(person_id)
                name  = info["name"]
                dept  = info["department"]
                color = (0, 200, 60)

                # ── Cross-section validation ──────────────────────────────
                if role == "student":
                    session_dept    = _ROLE_SESSION.get("dept", "").upper().strip()
                    session_section = _ROLE_SESSION.get("section", "").upper().strip()
                    session_course  = _ROLE_SESSION.get("course", "").upper().strip()
                    session_year    = _ROLE_SESSION.get("year", "").upper().strip()
                    student_dept    = info.get("department", "").upper().strip()
                    student_section = info.get("section", "").upper().strip()
                    student_course  = info.get("course", "").upper().strip()
                    student_year    = info.get("year", "").upper().strip()
                    student_active  = info.get("active", 1)

                    # Block deactivated students
                    if student_active == 0:
                        color = (0, 165, 255)  # orange
                        cv2.rectangle(display, (x, y), (x+w, y+h), color, 2)
                        cv2.putText(display, "Inactive Student",
                                    (x+4, y-8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
                        continue

                    # Check dept/section/course/year mismatch
                    wrong_class = False
                    mismatch_reason = ""
                    if session_dept and student_dept and student_dept != session_dept:
                        wrong_class = True
                        mismatch_reason = f"Dept: {student_dept} != {session_dept}"
                    elif session_section and student_section and student_section != session_section:
                        wrong_class = True
                        mismatch_reason = f"Sec: {student_section} != {session_section}"
                    elif session_course and student_course and student_course != session_course:
                        wrong_class = True
                        mismatch_reason = f"Course mismatch"
                    elif session_year and student_year and student_year != session_year:
                        wrong_class = True
                        mismatch_reason = f"Year mismatch"

                    if wrong_class:
                        # Draw orange box — do NOT mark attendance
                        color = (0, 140, 255)  # orange
                        cv2.rectangle(display, (x, y), (x+w, y+h), color, 2)
                        cv2.rectangle(display, (x, y-30), (x+w, y), color, -1)
                        cv2.putText(display, f"Wrong Class: {name}",
                                    (x+4, y-18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255,255,255), 1)
                        cv2.putText(display, mismatch_reason,
                                    (x+4, y-5), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255,255,255), 1)
                        log.warning("[CROSS-SECTION] Blocked %s (%s) — %s",
                                    person_id, name, mismatch_reason)
                        _ROLE_SESSION["last_detection"] = {
                            "status":    "wrong_class",
                            "person_id": person_id,
                            "name":      name,
                            "reason":    mismatch_reason,
                            "time":      datetime.datetime.now().strftime("%H:%M:%S"),
                        }
                        continue  # skip — do NOT mark attendance
                # ── End cross-section validation ──────────────────────────

                # ── Liveness / anti-spoofing gate ─────────────────────────
                _is_live = True
                if _liveness_on and _liveness_available and _liveness_mod is not None:
                    try:
                        _live_score = _liveness_mod.passive_liveness_score(frame, (x, y, w, h))
                        _is_live = (_live_score >= _liveness_threshold)
                        if not _is_live:
                            # Draw red "SPOOF" label — do not mark
                            cv2.putText(display, f"SPOOF? score={_live_score:.2f}",
                                        (x+4, y+h+18),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 220), 1)
                            log.debug("[LIVENESS] Blocked %s score=%.2f", person_id, _live_score)
                    except Exception as _liv_exc:
                        log.debug("[LIVENESS] Error: %s — treating as live", _liv_exc)
                        _is_live = True  # fail open — do not block on liveness error
                if not _is_live:
                    continue  # skip marking — spoof detected
                # ── Confidence + confirmation buffer gate ─────────────────

                # FIX: attendance gate — 3-frame confirmation buffer before marking
                if conf >= _MARK_MIN:
                    if _is_marked(person_id):
                        # Already marked — reset counter, show duplicate info
                        _confirm_counts.pop(person_id, None)
                        _ROLE_SESSION["last_detection"] = {
                            "status":     "duplicate",
                            "person_id":  person_id,
                            "name":       name,
                            "department": dept,
                            "role":       role_label,
                            "confidence": round(conf, 4),
                            "time":       datetime.datetime.now().strftime("%H:%M:%S"),
                        }
                    else:
                        # Increment confirmation counter
                        _confirm_counts[person_id] = _confirm_counts.get(person_id, 0) + 1
                        if _confirm_counts[person_id] >= _CONFIRM_FRAMES_NEEDED:
                            _mark(person_id, name, dept, conf)
                            _confirm_counts.pop(person_id, None)  # reset after marking
                else:
                    # Confidence dropped — reset counter for this person
                    _confirm_counts.pop(person_id, None)

                lbl = f"{name} {int(conf * 100)}%"

                cv2.rectangle(display, (x, y), (x+w, y+h), color, 2)
                cv2.rectangle(display, (x, y-30), (x+w, y), color, -1)
                cv2.putText(display, lbl, (x+4, y-8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
            else:
                # FIX: unknown / low-confidence face → red box + "?" only, no name
                color = (0, 0, 200)
                cv2.rectangle(display, (x, y), (x+w, y+h), color, 2)
                cv2.putText(display, "?",
                            (x + w//2 - 8, y - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        # Reset confirmation counters for faces no longer visible
        if len(faces) == 0:
            _confirm_counts.clear()

        # Role badge overlay
        cv2.rectangle(display, (0, 0), (display.shape[1], 36), (20, 20, 30), -1)
        cv2.putText(display,
                    f"ROLE: {role_label.upper()}  |  {period}  |  {len(faces)} face(s)",
                    (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 180), 1)

        # Push frame to MJPEG queue
        try:
            ok, buf = cv2.imencode(".jpg", display, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ok:
                jpeg = buf.tobytes()
                fq = _sess._FRAME_QUEUE
                if fq.full():
                    try: fq.get_nowait()
                    except queue.Empty: pass
                try: fq.put_nowait(jpeg)
                except queue.Full: pass
        except Exception:
            pass

    # ── Cleanup ─────────────────────────────────────────────────
    cap.release()
    _ROLE_SESSION["running"]         = False
    _sess._SESSION_STATE["running"]  = False
    _sess._SESSION_STATE["thread"]   = None
    log.info("[ROLE-WORKER] Exited. role=%s period=%s", role, period)


# =============================================================
# ROUTE REGISTRATION
# =============================================================

def register_role_attendance_routes(app, db_module, config_module):
    """Call from api.py → create_app() to attach all role attendance routes."""
    from auth_utils import get_current_user

    DB_PATH = os.path.join(config_module.BASE_DIR, "attendance.db")

    try:
        LBPH_THRESHOLD = float(getattr(config_module, "LBPH_THRESHOLD", 120))
    except Exception:
        LBPH_THRESHOLD = 120.0

    ROLE_CONFIG = {
        "student": {
            "label":       "Student",
            "model_path":  config_module.LBPH_MODEL,
            "labels_path": config_module.LBPH_LABELS,
            "model_type":  "yml",
            "table":       "student_attendance",
            "id_col":      "student_id",
            "lookup_table":"students",
            "lookup_id":   "student_id",
        },
        "staff": {
            "label":       "Staff",
            "model_path":  config_module.STAFF_LBPH_MODEL,
            "labels_path": None,
            "model_type":  "pkl",
            "table":       "staff_attendance",
            "id_col":      "staff_id",
            "lookup_table":"faculty",
            "lookup_id":   "fac_id",
        },
        "hod": {
            "label":       "HOD",
            "model_path":  config_module.HOD_LBPH_MODEL,
            "labels_path": None,
            "model_type":  "pkl",
            "table":       "hod_attendance",
            "id_col":      "hod_id",
            "lookup_table":"hods",    # BUG-2 FIX: was "hod" → table doesn't exist; real table is "hods"
            "lookup_id":   "hod_id",
        },
    }

    # ─────────────────────────────────────────────────────────
    # GET /api/enrollment/counts
    # ─────────────────────────────────────────────────────────
    @app.get("/api/enrollment/counts")
    def enrollment_counts():
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            students = conn.execute(
                "SELECT COUNT(*) FROM students WHERE active=1"
            ).fetchone()[0]
            try:
                faculty = conn.execute("SELECT COUNT(*) FROM faculty").fetchone()[0]
            except Exception:
                faculty = 0
            try:
                hods = conn.execute("SELECT COUNT(*) FROM hods").fetchone()[0]
            except Exception:
                hods = 0
            conn.close()
            return {"students": students, "faculty": faculty, "hods": hods}
        except Exception as exc:
            return {"students": 0, "faculty": 0, "hods": 0, "error": str(exc)}

    # ─────────────────────────────────────────────────────────
    # GET /api/staff/by-dept?dept=CSE
    # ─────────────────────────────────────────────────────────
    @app.get("/api/staff/by-dept")
    def staff_by_dept(dept: str = ""):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            if dept:
                rows = conn.execute(
                    "SELECT fac_id, name FROM faculty WHERE dept=? ORDER BY name",
                    (dept.upper(),)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT fac_id, name FROM faculty ORDER BY name"
                ).fetchall()
            conn.close()
            return {"staff": [{"fac_id": r["fac_id"], "name": r["name"]} for r in rows]}
        except Exception as exc:
            log.warning("staff/by-dept error: %s", exc)
            return {"staff": [], "error": str(exc)}

    # ─────────────────────────────────────────────────────────
    # GET /api/hod/by-dept?dept=CSE
    # ─────────────────────────────────────────────────────────
    @app.get("/api/hod/by-dept")
    def hod_by_dept(dept: str = ""):
        """
        FIX v10.5 — Fuzzy dept matching.
        The terminal enrollment prompt stores whatever the user typed
        (e.g. "computer science", "cse", blank).
        The frontend always sends the short key (e.g. "CSE").
        We try four strategies so HODs are never invisible due to a dept typo.
        """
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            rows = []
            if dept:
                dept_key = dept.strip().upper().split()[0]  # "CSE - Computer..." → "CSE"

                # 1. Exact match (stored correctly as "CSE")
                rows = conn.execute(
                    "SELECT hod_id, name FROM hods WHERE active=1 AND UPPER(dept)=? ORDER BY name",
                    (dept_key,)
                ).fetchall()

                # 2. Contains match (stored as "CSE - Computer..." or "Computer Science (CSE)")
                if not rows:
                    rows = conn.execute(
                        "SELECT hod_id, name FROM hods WHERE active=1 AND UPPER(dept) LIKE ? ORDER BY name",
                        (f"%{dept_key}%",)
                    ).fetchall()

                # 3. Dept is blank / user skipped — return all active HODs
                if not rows:
                    rows = conn.execute(
                        "SELECT hod_id, name FROM hods WHERE active=1 AND (dept='' OR dept IS NULL) ORDER BY name"
                    ).fetchall()

                # 4. Absolute fallback — any active HOD (avoids empty UI)
                if not rows:
                    rows = conn.execute(
                        "SELECT hod_id, name FROM hods WHERE active=1 ORDER BY name"
                    ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT hod_id, name FROM hods WHERE active=1 ORDER BY name"
                ).fetchall()
            conn.close()
            return {"hods": [{"hod_id": r["hod_id"], "name": r["name"]} for r in rows]}
        except Exception as exc:
            log.warning("hod/by-dept error: %s", exc)
            return {"hods": [], "error": str(exc)}

    # ─────────────────────────────────────────────────────────
    # POST /api/role/session/start
    # ─────────────────────────────────────────────────────────
    @app.post("/api/role/session/start")
    def role_session_start(req: RoleSessionReq, current_user: dict = Depends(get_current_user)):
        global _ROLE_SESSION

        with _ROLE_LOCK:
            # Check for stale thread
            t = _ROLE_SESSION.get("thread")
            if t and not t.is_alive():
                _ROLE_SESSION["running"] = False
                _ROLE_SESSION["thread"]  = None

            if _ROLE_SESSION.get("running"):
                raise HTTPException(409, "Session already running. Stop it first.")

            role = req.role.lower().strip()
            if role not in ("student", "staff", "hod"):
                raise HTTPException(400, "role must be: student | staff | hod")
            if not req.dept.strip():
                raise HTTPException(400, "Department is required")

            role_cfg = ROLE_CONFIG[role]

            # Friendly model-missing error
            if not os.path.exists(role_cfg["model_path"]):
                raise HTTPException(
                    422,
                    f"Model not found for {role_cfg['label']}: "
                    f"{os.path.basename(role_cfg['model_path'])}. "
                    f"Please run Training first (Main Menu → Train All Models)."
                )

            # Build period string
            ts = datetime.datetime.now().strftime("%H%M")
            if role == "student":
                sem    = f"_S{req.semester}" if req.semester else ""
                period = req.period or (
                    f"M_{ts}_student_{req.dept}_{req.course}_Y{req.year}{sem}_Sec{req.section}"
                )
            elif role == "staff":
                sid    = f"_{req.staff_id}" if req.staff_id else ""
                period = req.period or f"M_{ts}_staff_{req.dept}{sid}"
            else:
                period = req.period or f"M_{ts}_hod_{req.dept}"

            _ROLE_SESSION.update({
                "running":    True,
                "role":       role,
                "period":     period,
                "dept":       req.dept.upper(),
                "error":      None,
                "started_at": datetime.datetime.now().isoformat(),
                "course":     req.course,
                "year":       req.year,
                "semester":   req.semester,
                "section":    req.section,
                "staff_id":   req.staff_id,
                "hod_id":     req.hod_id,
            })

            t = threading.Thread(
                target=_role_worker,
                args=(role, role_cfg, period, DB_PATH, LBPH_THRESHOLD),
                daemon=True,
                name=f"RoleWorker-{role}-{period}",
            )
            _ROLE_SESSION["thread"] = t
            t.start()

        log.info("[ROLE-SESSION] Started role=%s period=%s", role, period)
        user_name = current_user.get("username", "unknown") if current_user else "unknown"
        log.info("[ROLE-SESSION] Started by user=%s role=%s period=%s", user_name, role, period)
        _ROLE_SESSION["started_by"] = user_name
        return {
            "status":  "started",
            "role":    role,
            "period":  period,
            "stream":  "/video_feed",
            "message": f"{role_cfg['label']} attendance session started",
        }

    # ─────────────────────────────────────────────────────────
    # POST /api/role/session/stop
    # ─────────────────────────────────────────────────────────
    @app.post("/api/role/session/stop")
    def role_session_stop(current_user: dict = Depends(get_current_user)):
        global _ROLE_SESSION
        with _ROLE_LOCK:
            _ROLE_SESSION["running"] = False

        # Also stop the shared _SESSION_STATE so /video_feed goes idle
        try:
            import attendance_session as _sess
            _sess._SESSION_STATE["running"] = False
        except Exception:
            pass

        t = _ROLE_SESSION.get("thread")
        if t and t.is_alive():
            t.join(timeout=3.0)
        _ROLE_SESSION["thread"] = None
        log.info("[ROLE-SESSION] Stopped")
        user_name = current_user.get("username", "unknown") if current_user else "unknown"
        log.info("[ROLE-SESSION] Stopped by user=%s", user_name)

        # Auto-mark absent students when session stops
        try:
            with _ROLE_LOCK:
                _s = dict(_ROLE_SESSION)
            _role  = _s.get("role", "")
            _dept  = _s.get("dept", "")
            _per   = _s.get("period", "")
            _sect  = _s.get("section", "")
            _crs   = _s.get("course", "")
            _yr    = _s.get("year", "")
            _sem   = _s.get("semester", "")

            if _role == "student" and _dept and _per:
                _today = datetime.datetime.now().strftime("%Y-%m-%d")
                _now   = datetime.datetime.now().strftime("%H:%M:%S")
                conn   = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
                # Get all active students in this section
                if _sect:
                    all_students = conn.execute(
                        "SELECT student_id, name, department, section, course, year "
                        "FROM students WHERE department=? AND section=? "
                        "AND course=? AND year=? AND active=1",
                        (_dept, _sect.upper(), _crs.upper(), _yr.upper())
                    ).fetchall()
                else:
                    all_students = conn.execute(
                        "SELECT student_id, name, department, section, course, year "
                        "FROM students WHERE department=? AND active=1",
                        (_dept,)
                    ).fetchall()
                # Mark absent those not already in student_attendance for this period
                absent_count = 0
                for stu in all_students:
                    existing = conn.execute(
                        "SELECT id FROM student_attendance "
                        "WHERE student_id=? AND period=? AND date=?",
                        (stu[0], _per, _today)
                    ).fetchone()
                    if not existing:
                        conn.execute(
                            "INSERT OR IGNORE INTO student_attendance "
                            "(student_id, name, role, department, course, year, "
                            "section, semester, date, time, period, status, confidence) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (stu[0], stu[1], "Student", stu[2],
                             _crs, _yr, _sect, _sem,
                             _today, _now, _per, "Absent", 0.0)
                        )
                        absent_count += 1
                conn.commit()
                conn.close()
                log.info("[SESSION-STOP] Auto-marked %d students Absent for period=%s",
                         absent_count, _per)
        except Exception as _abs_exc:
            log.warning("[SESSION-STOP] Auto-absent failed: %s", _abs_exc)

        return {"status": "stopped", "message": "Session stopped"}

    # ─────────────────────────────────────────────────────────
    # GET /api/role/session/status
    # ─────────────────────────────────────────────────────────
    @app.get("/api/role/session/status")
    def role_session_status(current_user: dict = Depends(get_current_user)):
        global _ROLE_SESSION
        with _ROLE_LOCK:
            s = dict(_ROLE_SESSION)

        # Auto-detect dead thread
        t = s.get("thread")
        if t and not t.is_alive():
            s["running"] = False

        role   = s.get("role") or "student"
        period = s.get("period") or ""
        dept   = s.get("dept") or ""

        tbl_map = {
            "student": ("student_attendance", "student_id"),
            "staff":   ("staff_attendance",   "staff_id"),
            "hod":     ("hod_attendance",      "hod_id"),
        }
        tbl, id_col = tbl_map.get(role, ("student_attendance", "student_id"))

        marked: list = []
        if period:
            try:
                today = datetime.datetime.now().strftime("%Y-%m-%d")
                conn  = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                rows  = conn.execute(
                    f"SELECT {id_col} AS uid, name, department, time, date, status, confidence "
                    f"FROM {tbl} WHERE period=? AND date=? ORDER BY time DESC",
                    (period, today)
                ).fetchall()
                conn.close()
                marked = [dict(r) for r in rows]
            except Exception as exc:
                log.warning("role/status db error: %s", exc)

        # Total enrolled — filtered by section/course/year for students;
        # filtered by dept + active for staff; by dept for hods.
        total = 0
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
            _section  = s.get("section",  "") or ""
            _course   = s.get("course",   "") or ""
            _year     = s.get("year",     "") or ""
            if role == "student" and dept and _section:
                total = conn.execute(
                    "SELECT COUNT(*) FROM students "
                    "WHERE department=? AND section=? AND course=? AND year=? AND active=1",
                    (dept.upper(), _section.upper(), _course.upper(), _year.upper())
                ).fetchone()[0]
            elif role == "student" and dept:
                total = conn.execute(
                    "SELECT COUNT(*) FROM students WHERE department=? AND active=1",
                    (dept.upper(),)
                ).fetchone()[0]
            elif role == "staff" and dept:
                total = conn.execute(
                    "SELECT COUNT(*) FROM faculty WHERE dept=? AND active=1",
                    (dept.upper(),)
                ).fetchone()[0]
            elif role == "staff":
                total = conn.execute(
                    "SELECT COUNT(*) FROM faculty WHERE active=1"
                ).fetchone()[0]
            elif role == "hod" and dept:
                total = conn.execute(
                    "SELECT COUNT(*) FROM hods WHERE dept=?",
                    (dept.upper(),)
                ).fetchone()[0]
            elif role == "hod":
                total = conn.execute(
                    "SELECT COUNT(*) FROM hods"
                ).fetchone()[0]
            conn.close()
        except Exception as _tc_exc:
            log.warning("[ROLE-STATUS] total-count query failed: %s", _tc_exc)

        return {
            "running":        s.get("running", False),
            "role":           role,
            "period":         period,
            "dept":           dept,
            "started_at":     s.get("started_at"),
            "error":          s.get("error", ""),
            "model_warning":  s.get("model_warning", ""),   # BUG-5: surfaces invalid-label alert
            "last_detection": s.get("last_detection"),       # drives frontend "Last Detection" card
            "already_marked": marked,
            "marked_count":   len(marked),
            "total_students": total,
            "absent_count":   max(0, total - len(marked)),
        }

    log.info("Role attendance routes registered (v10.2) ✓")
    log.info("  → GET  /api/enrollment/counts")
    log.info("  → GET  /api/staff/by-dept")
    log.info("  → GET  /api/hod/by-dept")
    log.info("  → POST /api/role/session/start")
    log.info("  → POST /api/role/session/stop")
    log.info("  → GET  /api/role/session/status")





import cv2
import numpy as np

# ─────────────────────────────────────────────────────────────
# INSERT after the grab/flush loop in _role_worker():
# ─────────────────────────────────────────────────────────────

def _push_warmup_frame_to_queue():
    """
    Call this ONCE right after the camera flush loop in _role_worker(),
    before entering the recognition while-loop.

    Prevents generate_frames() from timing out (and serving _OFFLINE_JPEG)
    during the gap between camera open and the first real frame being captured.
    """
    import queue as _queue

    # Import the shared queue from attendance_session (adjust import path as needed)
    import attendance_session as _sess

    warmup = np.zeros((480, 640, 3), dtype=np.uint8)
    warmup[:] = (18, 18, 18)
    cv2.putText(warmup, "Camera Initialising...",
                (130, 225), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (60, 180, 60), 2)
    ok, buf = cv2.imencode(".jpg", warmup, [cv2.IMWRITE_JPEG_QUALITY, 60])
    if ok:
        try:
            _sess._FRAME_QUEUE.put_nowait(buf.tobytes())
        except _queue.Full:
            pass   # Queue already has frames — no problem


# ─────────────────────────────────────────────────────────────
# HOW IT FITS IN _role_worker() — BEFORE/AFTER:
# ─────────────────────────────────────────────────────────────

# BEFORE (existing code):
#
#   cap = cv2.VideoCapture(cam_idx)
#   ...
#   for _ in range(8):        # flush stale frames
#       cap.grab()
#       time.sleep(0.02)
#
#   while _ROLE_SESSION.get("running"):   # ← recognition loop starts here
#       ret, frame = cap.read()
#       ...

# AFTER (with fix inserted):
#
#   cap = cv2.VideoCapture(cam_idx)
#   ...
#   for _ in range(8):        # flush stale frames
#       cap.grab()
#       time.sleep(0.02)
#
#   _push_warmup_frame_to_queue()    # ← ADD THIS SINGLE CALL
#
#   while _ROLE_SESSION.get("running"):   # recognition loop unchanged
#       ret, frame = cap.read()
#       ...