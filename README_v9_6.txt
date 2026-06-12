═══════════════════════════════════════════════════════════════════
  SMART ATTENDANCE SYSTEM  v9.6  —  EduTrack Pro Integration
  Backend: Python / FastAPI / SQLite / LBPH + dlib
  Frontend: EduTrack Pro (HTML + CSS + JS)
═══════════════════════════════════════════════════════════════════

WHAT'S NEW IN v9.6
══════════════════

  ■ Full EduTrack Pro frontend integrated into the backend.
    Open http://localhost:8000/app — no separate server needed.

  ■ New /api/* REST endpoints bridge the frontend to the database:
    POST /api/login               → Admin / Faculty login with JWT
    GET  /api/students            → All students from SQLite
    POST /api/students            → Add student record
    DELETE /api/students/{id}     → Remove student
    GET  /api/attendance/today    → Today's marked attendance
    GET  /api/attendance/summary  → Per-student stats (n days)
    POST /api/attendance/override → Teacher / Incharge correction
    GET  /api/session/status      → Live session poll
    POST /api/session/start       → Start face recognition thread
    POST /api/session/stop        → Stop session
    POST /api/train               → Kick off LBPH + dlib training
    GET  /api/train/status        → Poll training progress
    GET  /api/analytics/summary   → Dashboard KPI object
    GET  /api/timetable           → Period list from DB
    GET  /api/settings            → Config thresholds
    POST /api/settings            → Update thresholds live
    GET  /api/export/csv          → Download attendance CSV
    GET  /video_feed              → MJPEG live camera stream
    GET  /app  or  /              → EduTrack Pro frontend

  ■ Single source of truth: ALL data lives in attendance.db (SQLite).
    Frontend reads from and writes to the same database as the
    face recognition engine and the CLI menu.

  ■ CORS enabled for all origins — works whether the frontend is
    served by FastAPI or opened directly in a browser.

  ■ Training runs in a background thread — the UI stays responsive
    and you can poll /api/train/status to see live log output.

  ■ MJPEG video stream (/video_feed) is displayed directly in the
    frontend <img> tag — real camera feed with face bounding boxes,
    name labels, and confidence scores.


FOLDER STRUCTURE
════════════════

  smart_attendance/
  ├── frontend/               ← EduTrack Pro frontend (served by FastAPI)
  │   ├── index.html          ← Main SPA
  │   ├── style.css           ← Mild happy colors theme
  │   └── app.js              ← All frontend logic, wired to /api/*
  │
  ├── data/
  │   ├── dataset/            ← Grayscale face images per student
  │   └── known_faces/        ← Colour face images for dlib
  │
  ├── models/                 ← Trained model files (auto-created)
  │   ├── lbph_model.yml
  │   ├── lbph_labels.pkl
  │   ├── face_encodings.pkl
  │   └── twin_model.pkl
  │
  ├── logs/                   ← Application logs (auto-created)
  ├── attendance/             ← Session exports (auto-created)
  │
  ├── main.py           ← Entry point — run this
  ├── api.py            ← FastAPI server with all endpoints
  ├── database.py       ← SQLite operations (single source of truth)
  ├── config.py         ← All thresholds and paths
  ├── attendance_session.py ← Camera session thread + MJPEG
  ├── recognizer.py     ← LBPH + dlib recognition engine
  ├── lighting.py       ← Dark skin preprocessing
  ├── enroll.py         ← Student enrolment UI
  ├── train.py          ← Model training
  ├── liveness.py       ← Anti-spoofing
  ├── twin_analysis.py  ← Twin disambiguation
  ├── .env.example      ← Configuration template
  └── requirements.txt  ← Python dependencies


SETUP (First Time)
══════════════════

  1. Install dependencies
     ─────────────────────
     pip install -r requirements.txt

     On Windows, if dlib fails:
       pip install cmake
       pip install dlib
       pip install face-recognition

  2. Create your .env file
     ──────────────────────
     cp .env.example .env
     # Edit .env — change passwords, set CAMERA_INDEX, etc.

  3. Verify setup
     ─────────────
     python main.py → [10] System Diagnostics


WORKFLOW
════════

  Step 1 — Enrol students
  ─────────────────────────
     python main.py → [1] Enrol New Student

     The camera opens. For each of 5 poses:
       - Press SPACE to start recording
       - Hold still — 40 images captured per pose
       - Press ESC to skip a pose

     ► LIGHTING TIP: Face a window or lamp.
       For dark skin, add extra light near the face.
       The screen shows "VERY DARK" if lighting is poor.

  Step 2 — Train models
  ──────────────────────
     python main.py → [2] Train All Models
     OR
     python main.py → [4] Launch frontend → click "Train Models"

     Check self-test output:
       EXCELLENT = dist < 20  → perfect, conf ≈ 80%+ at runtime
       GOOD      = dist < 40  → conf ≈ 60–80% at runtime
       PASS      = dist < 100 → conf ≈ 30–50% — consider re-enrol
       WARN      = dist > 100 → will show Unknown — re-enrol

  Step 3 — Start the system
  ──────────────────────────
     python main.py → [4] Start API + Frontend

     Opens:
       http://localhost:8000/app     ← EduTrack Pro dashboard
       http://localhost:8000/docs    ← Swagger API explorer
       http://localhost:8000/video_feed ← Raw MJPEG stream


FRONTEND LOGIN
══════════════

  Admin Portal (full access):
    Email:    admin@college.edu  (or just "admin")
    Password: admin123
    Role:     Admin / HOD / Class Incharge

  Teacher Portal:
    Email:    teacher
    Password: teacher123

  Faculty Portal (separate tab):
    Faculty ID: FAC001 (any ID enrolled in DB)
    Password:   fac@2025  (default dev password)

  ► These credentials are set in your .env file.
    ADMIN_PASSWORD and TEACHER_PASSWORD control the admin/teacher logins.


WHAT EACH FRONTEND PAGE DOES
══════════════════════════════

  Dashboard
    Shows live KPIs from /api/analytics/summary:
    total students, present today, avg attendance, critical count.
    Today's attendance table pulled from /api/attendance/today.

  Take Attendance
    Enter a period name (e.g. Period_1) and click Start Session.
    The backend opens the webcam, runs LBPH + dlib recognition,
    marks attendance in the database, and streams the camera feed
    as MJPEG to the <img> element in the page.
    The session log polls /api/session/status every 2.5 seconds.

  Students
    Lists all students from /api/students (SQLite database).
    Add new students (DB record only — then enrol face separately).
    Delete students.

  Timetable
    Shows period schedule from /api/timetable (configured in
    config.py DEFAULT_PERIODS, seeded into SQLite on first run).

  Overrides
    Override a student's attendance status with a reason.
    Select the student from the live database list.
    The record is saved via /api/attendance/override → SQLite.
    Supports Staff, Class Incharge, and Admin override types.

  Reports
    Attendance summary from /api/attendance/summary.
    Period-wise bar chart from /api/analytics/period.
    CSV export from /api/export/csv.

  Alerts
    Computed from the summary data:
    < 75%: Student alert
    < 70%: Class Incharge alert
    < 65%: Critical — HOD alert

  Settings
    Read and update LBPH_THRESHOLD, DLIB_DISTANCE, etc.
    Changes apply immediately (in-memory). Restart to persist
    permanently (or add .env entries).

  Train Models
    Kicks off LBPH + dlib training as a background thread.
    Progress is streamed to the terminal log box every 3 seconds.


API AUTHENTICATION
══════════════════

  All /api/* endpoints (except /app, /video_feed) require a JWT.
  The frontend stores the token in memory (no localStorage).

  Get a token:
    POST /api/login
    { "email": "admin", "password": "admin123", "role": "admin" }

    Response: { "access_token": "...", "role": "admin" }

  Use the token:
    Authorization: Bearer <token>


DATABASE SCHEMA
════════════════

  attendance.db (SQLite)
  ├── students        — student_id, name, roll_number, section, mobile, twin_of, active
  ├── attendance      — student_id, name, period, date, time, confidence, engine
  ├── override_log    — student_id, period, action, note, teacher, created_at
  ├── timetable       — period_name, start_time, end_time, active
  ├── audit_log       — user_name, action, resource, detail, ip_address
  └── twin_analysis_log — student_id, twin_id, decision, final_confidence

  All frontend operations write directly to this database.
  The face recognition engine also writes to the same database.


THRESHOLD TUNING
════════════════

  Press D in the attendance camera window to see LBPH distances.

  Distance 0–30:   Excellent — conf ≈ 70–100%  (green box)
  Distance 30–60:  Good      — conf ≈ 40–70%   (green box)
  Distance 60–100: Borderline — conf ≈ 0–40%   (orange box)
  Distance 100+:   Rejected as Unknown           (red box)

  If enrolled student shows Unknown:
    → Raise LBPH_THRESHOLD in .env (try 130, 140)
    → OR re-enrol in better lighting

  If unknown person is matched to a student:
    → Lower LBPH_UNKNOWN_MARGIN in .env (try 80, 70)
    → Ensure training ran after enrolment ([2] Train)


TROUBLESHOOTING
════════════════

  ■ "Camera not responding" on Windows:
    The backend uses CAP_DSHOW backend automatically.
    Set CAMERA_INDEX=1 in .env if you have two cameras.
    Close Teams/Zoom before running.

  ■ Frontend shows "Login failed":
    Check that the API server is running (option [4]).
    Check that admin/teacher passwords match .env.

  ■ "No students yet" in frontend:
    Enrol at least one student with [1] Enrol.
    Then train with [2] Train before running sessions.

  ■ Low confidence (< 40%):
    Re-enrol with more light on the face (200+ images preferred).
    Check train self-test distances — target dist < 40.
    See THRESHOLD TUNING section above.

  ■ Training fails in frontend:
    The background thread logs errors to /api/train/status.
    You can also run training via CLI: [2] Train All Models.

═══════════════════════════════════════════════════════════════════
  Credentials:  admin / admin123  |  teacher / teacher123
  Dashboard:    http://localhost:8000/app
  API Docs:     http://localhost:8000/docs
  Video Feed:   http://localhost:8000/video_feed
═══════════════════════════════════════════════════════════════════
