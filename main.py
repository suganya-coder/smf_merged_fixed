

# =============================================================
# main.py  —  Smart Attendance System  v9.6
#
# Entry point for all system operations.
# Run:  python main.py
#
# Option [4] starts the REST API + EduTrack Frontend together.
# The frontend is served at:  http://localhost:8000/app
# =============================================================
import os
import sys
import logging

# ── Create logs/ dir before configuring FileHandler ──────────
_BASE    = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR = os.path.join(_BASE, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers= [
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(_LOG_DIR, "attendance.log"),
            encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

# ── Import project modules ────────────────────────────────────
import config
config.init_dirs()

import database as db   # SQLite — single source of truth
db.init_db()            # create tables if first run

# Auto-seed full timetable (idempotent — skips if already seeded)
try:
    from timetable_seed import seed_all_timetables, _db, _ensure_tables
    # Check if faculty table already has data
    with _db() as _chk_conn:
        _ensure_tables(_chk_conn)
        _fac_count = _chk_conn.execute("SELECT COUNT(*) FROM faculty WHERE active=1").fetchone()[0]
    if _fac_count == 0:
        print("  Auto-seeding timetable data (first run)...")
        seed_all_timetables(force=False)
    else:
        print(f"  Timetable already seeded ({_fac_count} faculty found). Skipping.")
except Exception as _seed_err:
    log.warning("Timetable auto-seed skipped: %s", _seed_err)
    # Still try basic seed
    try:
        from timetable_seed import seed_all_timetables
        seed_all_timetables(force=False)
    except Exception:
        pass


# =============================================================
# HELPERS
# =============================================================
def _check_config():
    warnings = []
    if not getattr(config, 'ADMIN_PASSWORD', None):
        warnings.append("ADMIN_PASSWORD not set in .env")
    if not getattr(config, 'TEACHER_PASSWORD', None):
        warnings.append("TEACHER_PASSWORD not set in .env")
    if warnings:
        print("\n  ⚠  CONFIGURATION WARNINGS:")
        for w in warnings:
            print(f"     • {w}")
        print("     Copy .env.example to .env and edit it.\n")


def _check_db():
    try:
        students = db.get_all_students()
        print(f"  DB connected — {len(students)} student(s)\n")
        return True
    except Exception as e:
        print(f"\n  DB error: {e}\n")
        return False


def _menu():
    print("""
╔══════════════════════════════════════════════════════════════╗
║       SMART ATTENDANCE SYSTEM  v10.0  —  EduTrack Pro        ║
║  SQLite  |  LBPH  |  dlib  |  FastAPI  |  EduTrack Frontend ║
╠══════════════════════════════════════════════════════════════╣
║  [1] Enrol (Student/Faculty/HOD) [7] Teacher Override        ║
║  [2] Train Model (Selective) ★  [8] Register Twin Pair      ║
║  [3] Start Attendance Session ★ [9] View Twin Analysis Log  ║
║  [4] Start API + Frontend       [10] System Diagnostics     ║
║  [5] View Today's Attendance    [11] Train Twin Model Only  ║
║  [6] View Attendance Report     [12] Debug: Test LBPH       ║
║                                 [0]  Exit                   ║
╠══════════════════════════════════════════════════════════════╣
║  ★ Option [3]: Asks Role → Student / Staff / HOD            ║
║  ★ Option [2]: Train HOD/Staff/Student selectively          ║
║  Option [4] opens: http://localhost:8000/app                 ║
║    Admin login: admin / admin123                             ║
║    Teacher:     teacher / teacher123                         ║
╚══════════════════════════════════════════════════════════════╝""")


# =============================================================
# ACTIONS
# =============================================================
def do_enrol():
    print("\n" + "=" * 55)
    print("  Select Role for Enrollment")
    print("=" * 55)
    print("  1. Student")
    print("  2. Faculty")
    print("  3. HOD")
    print("  0. Cancel")
    print("=" * 55)

    role = input("  Enter choice (1/2/3 or student/faculty/hod): ").strip().lower()

    if role in ("1", "student"):
        from enroll import enroll_student
        enroll_student()

    elif role in ("2", "faculty"):
        from enroll_staff import enroll_faculty
        enroll_faculty()

    elif role in ("3", "hod"):
        from enroll_hod import enroll_hod
        enroll_hod()

    elif role in ("0", "cancel", ""):
        print("  Enrollment cancelled.")

    else:
        print(f"  Invalid choice '{role}'. Please enter 1, 2, or 3.")


def do_train():
    """
    Option [2] — Selective Model Training.

    Instead of retraining everyone, asks:
      1  HOD
      2  Staff
      3  Student
      0  Cancel

    Shows Already Trained / Not Trained status, prompts for ID,
    and trains ONLY that person — appending to the existing model.

    Fallback: if train_selective is unavailable, runs full train_all().
    """
    try:
        from train_selective import selective_train_menu
        selective_train_menu()
    except ImportError as _e:
        log.warning("train_selective not found (%s) — running full train_all()", _e)
        from train import train_all
        train_all()


def do_session():
    """
    Option [3] — Role-Based Attendance Session.
    Asks: Student / Staff / HOD, then loads the matching
    LBPH model and marks attendance in the correct DB table.
    """
    from role_attendance_session import run_role_session
    run_role_session()


def do_api():
    """Start the FastAPI server which also serves the EduTrack frontend."""
    print("  Starting API + EduTrack Frontend...")
    print("  Frontend will be at: http://localhost:8000/app")
    from api import run_api
    run_api()


def do_today():
    period = input("  Filter by period (blank=all): ").strip() or None
    rows   = db.get_today_attendance(period)
    if not rows:
        print("  No attendance recorded today.")
    else:
        print(f"\n  {'Name':<20} {'ID':<15} {'Period':<12} "
              f"{'Time':<10} {'Conf':>6} {'Engine'}")
        print("  " + "-" * 75)
        for r in rows:
            name   = r.get("name",       "?")
            sid    = r.get("student_id", "?")
            period = r.get("period",     "?")
            tm     = str(r.get("time",   "?"))[:8]
            conf   = int(float(r.get("confidence", 0)) * 100)
            eng    = r.get("engine", "?")
            print(f"  {name:<20} {sid:<15} {period:<12} "
                  f"{tm:<10} {conf:>5}% {eng}")
    print()


def do_report():
    days = input("  Days to include [30]: ").strip()
    days = int(days) if days.isdigit() else 30
    rows = db.get_attendance_summary(days)
    print(f"\n  Attendance Report — Last {days} days")
    print(f"  {'Name':<20} {'Roll':<15} {'Section':<9} "
          f"{'Present':>8} {'Twin':>6}")
    print("  " + "-" * 65)
    for r in rows:
        print(f"  {r.get('name','?'):<20} "
              f"{r.get('roll_number','?'):<15} "
              f"{r.get('section','?'):<9} "
              f"{r.get('present_count',0):>8} "
              f"{'Yes' if r.get('is_twin') else '-':>6}")
    print()


def do_override():
    sid    = input("  Student ID     : ").strip()
    period = input("  Period         : ").strip()
    action = input("  Action (mark_present/mark_absent): ").strip()
    note   = input("  Note           : ").strip()
    try:
        db.teacher_override(sid, period, action, note)
        print(f"  Override applied: {action} for {sid}")
    except Exception as e:
        print(f"  Error: {e}")


def do_register_twin():
    print("\n  Register a twin pair (both must be enrolled first).")
    id1 = input("  Student ID 1: ").strip()
    id2 = input("  Student ID 2: ").strip()
    if not id1 or not id2:
        print("  ERROR: Both IDs required.")
        return
    db.register_twin_pair(id1, id2)
    print(f"  Twin pair registered: {id1} <-> {id2}")
    print(f"  → Run [11] to train the Twin SVM model.")


def do_twin_log():
    rows = db.get_twin_analysis_log(days=7)
    print(f"\n─── Twin Analysis Log (last 7 days) ───")
    if not rows:
        print("  No twin analysis records.")
    for r in rows[:20]:
        print(f"  {str(r.get('date','?')):<12} "
              f"{r.get('student_name', r.get('name', '?')):<18} "
              f"vs {r.get('partner_name', r.get('twin_id', '?')):<18} "
              f"{r.get('decision','?'):<10} "
              f"{float(r.get('final_confidence',0))*100:.0f}%")
    print()


def do_diagnostics():
    print("\n─── System Diagnostics ───")

    for label, path in [
        ("LBPH Model",     config.LBPH_MODEL),
        ("LBPH Labels",    config.LBPH_LABELS),
        ("dlib Encodings", config.DLIB_ENCODINGS),
        ("Twin Model",     config.TWIN_MODEL),
    ]:
        exists = os.path.exists(path)
        status = "✓" if exists else "✗ MISSING"
        size   = f"({os.path.getsize(path)//1024} KB)" if exists else ""
        print(f"  {label:<20} {status} {size}")

    students = db.get_all_students()
    print(f"\n  Students in DB: {len(students)}")
    for s in students:
        print(f"    {s.get('student_id','?'):<20} {s.get('name','?'):<20} "
              f"twin={'Yes' if s.get('is_twin') else '-'}")

    print(f"\n  Thresholds:")
    print(f"    LBPH_THRESHOLD   = {config.LBPH_THRESHOLD}")
    print(f"    DLIB_DISTANCE    = {config.DLIB_DISTANCE}")
    print(f"    MIN_CONFIDENCE   = {config.MIN_CONFIDENCE_PCT}%")
    print(f"    LIVENESS_ON      = {config.LIVENESS_ON}")

    print(f"\n  Dataset image counts:")
    try:
        for sid in os.listdir(config.DATASET_DIR):
            p = os.path.join(config.DATASET_DIR, sid)
            if os.path.isdir(p):
                n = len([f for f in os.listdir(p) if f.endswith(".jpg")])
                print(f"    {sid}: {n} images")
    except Exception:
        pass

    print(f"\n  Frontend directory:")
    fdir = os.path.join(config.BASE_DIR, "frontend")
    if os.path.isdir(fdir):
        for f in os.listdir(fdir):
            sz = os.path.getsize(os.path.join(fdir, f))
            print(f"    {f}  ({sz//1024} KB)")
    else:
        print("  ✗ frontend/ folder NOT found")
    print()


def do_debug_lbph():
    try:
        import cv2
    except ImportError:
        print("  [12] Requires OpenCV: pip install opencv-contrib-python")
        return

    import pickle
    import numpy as np
    import lighting

    print("\n─── Debug: LBPH Live Test ───")
    if not os.path.exists(config.LBPH_MODEL):
        print("  ERROR: LBPH model not found. Run [2] to train.")
        return

    rec = cv2.face.LBPHFaceRecognizer_create()
    rec.read(config.LBPH_MODEL)
    with open(config.LBPH_LABELS, "rb") as f:
        label_map = pickle.load(f)

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    import platform
    backends = ([cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
                if platform.system() == "Windows" else [cv2.CAP_ANY])
    cap = None
    for bk in backends:
        c = cv2.VideoCapture(config.CAMERA_INDEX, bk)
        if c.isOpened():
            c.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            c.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            for _ in range(5): c.grab()
            ret, f = c.read()
            if ret and f is not None:
                cap = c; break
            c.release()
    if cap is None:
        print("  Camera not available.")
        return

    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    print(f"  Press Q/ESC to exit. Current threshold: {config.LBPH_THRESHOLD}")

    while True:
        ret, frame = cap.read()
        if not ret: break
        proc  = lighting.preprocess_frame(frame)
        gray  = cv2.cvtColor(proc, cv2.COLOR_BGR2GRAY)
        eq    = cv2.equalizeHist(gray)
        faces = cascade.detectMultiScale(eq, 1.1, 4, minSize=(50, 50))
        for (x, y, w, h) in faces:
            face  = gray[y:y+h, x:x+w]
            face  = cv2.resize(face, (160, 160))
            face  = clahe.apply(face)
            lb, lr = rec.predict(face)
            name  = label_map.get(lb, "?")
            match = "✓ MATCH" if lr < config.LBPH_THRESHOLD else "✗ Unknown"
            cv2.rectangle(frame, (x, y), (x+w, y+h),
                          (0,200,0) if lr < config.LBPH_THRESHOLD else (0,0,220), 2)
            cv2.putText(frame, f"{name}: dist={lr:.1f} {match}",
                        (x, y-8), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255,255,255), 1)
            print(f"  {name}: dist={lr:.1f} {match}")

        cv2.putText(frame, f"Threshold: {config.LBPH_THRESHOLD}  Q=exit",
                    (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,200,255), 1)
        cv2.imshow("LBPH Debug", frame)
        if cv2.waitKey(1) & 0xFF in (27, ord('q'), ord('Q')):
            break

    cap.release()
    cv2.destroyAllWindows()
    print(f"\n  TIP: If dist > {config.LBPH_THRESHOLD}, raise LBPH_THRESHOLD in .env\n")


# =============================================================
# MAIN
# =============================================================
def main():
    _check_config()
    _check_db()

    while True:
        _menu()
        choice = input("  Enter choice: ").strip()

        if   choice == "0":  print("  Goodbye."); break
        elif choice == "1":  do_enrol()
        elif choice == "2":  do_train()
        elif choice == "3":  do_session()
        elif choice == "4":  do_api()
        elif choice == "5":  do_today()
        elif choice == "6":  do_report()
        elif choice == "7":  do_override()
        elif choice == "8":  do_register_twin()
        elif choice == "9":  do_twin_log()
        elif choice == "10": do_diagnostics()
        elif choice == "11":
            from twin_analysis import train_twin_model
            train_twin_model()
        elif choice == "12": do_debug_lbph()
        else: print("  Invalid choice. Try again.")


if __name__ == "__main__":
    main()