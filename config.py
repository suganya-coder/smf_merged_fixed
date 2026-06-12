

# =============================================================
# config.py  —  Smart Attendance System  v9.3
# =============================================================
import os, platform, secrets

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR     = os.path.join(BASE_DIR, "data", "dataset")
KNOWN_FACES_DIR = os.path.join(BASE_DIR, "data", "known_faces")
MODEL_DIR       = os.path.join(BASE_DIR, "models")
ATTENDANCE_DIR  = os.path.join(BASE_DIR, "attendance")
LOG_DIR         = os.path.join(BASE_DIR, "logs")
STATIC_DIR      = os.path.join(BASE_DIR, "static")
FRONTEND_DIR    = os.path.join(BASE_DIR, "frontend")

os.makedirs(LOG_DIR, exist_ok=True)

LBPH_MODEL     = os.path.join(MODEL_DIR, "lbph_model.yml")
LBPH_LABELS    = os.path.join(MODEL_DIR, "lbph_labels.pkl")
DLIB_ENCODINGS = os.path.join(MODEL_DIR, "face_encodings.pkl")
TWIN_MODEL        = os.path.join(MODEL_DIR, "twin_model.pkl")
TRAINED_IDS_JSON  = os.path.join(MODEL_DIR, "trained_ids.json")  # selective-training registry
SKELETON_MODEL = os.path.join(MODEL_DIR, "skeleton_svm.pkl")

# ── STAFF / HOD MODELS ────────────────────────────────────────
STAFF_DATASET_DIR   = os.path.join(BASE_DIR, "data", "dataset", "staff")
HOD_DATASET_DIR     = os.path.join(BASE_DIR, "data", "dataset", "hod")
STAFF_FACES_DIR     = os.path.join(BASE_DIR, "data", "known_faces", "staff")
HOD_FACES_DIR       = os.path.join(BASE_DIR, "data", "known_faces", "hod")
STAFF_LBPH_MODEL    = os.path.join(MODEL_DIR, "staff_face_model.pkl")
HOD_LBPH_MODEL      = os.path.join(MODEL_DIR, "hod_face_model.pkl")

# ── REST API ──────────────────────────────────────────────────
API_HOST               = os.environ.get("API_HOST", "0.0.0.0")
API_PORT               = int(os.environ.get("API_PORT", "8000"))
API_SECRET_KEY         = os.environ.get("API_SECRET_KEY", "smf-super-secret-key-change-in-prod!")
API_TOKEN_EXPIRY_HOURS = int(os.environ.get("API_TOKEN_EXPIRY_HOURS", "8"))
ADMIN_USERNAME         = os.environ.get("ADMIN_USERNAME",   "admin")
ADMIN_PASSWORD         = os.environ.get("ADMIN_PASSWORD",   "Admin@123")
TEACHER_USERNAME       = os.environ.get("TEACHER_USERNAME", "teacher")
TEACHER_PASSWORD       = os.environ.get("TEACHER_PASSWORD", "teacher123")

# ── ROLE-BASED LOGIN CREDENTIALS ──────────────────────────────
HOD_PASSWORD           = os.environ.get("HOD_PASSWORD",      "hod123")
INCHARGE_PASSWORD      = os.environ.get("INCHARGE_PASSWORD", "incharge123")
FACULTY_DEFAULT_PASSWORD = os.environ.get("FACULTY_DEFAULT_PASSWORD", "fac@2025")

# F-12: CORS lockdown — read allowed origins from env var.
# Defaults include the local dev origins only (localhost variants used by
# the built-in server and common live-server / Vite dev ports).
# For production, set CORS_ORIGINS env var to your actual frontend domain only.
CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:8000,http://127.0.0.1:8000,"
    "http://127.0.0.1:5500,http://localhost:5500,http://localhost:8080"
).split(",") if o.strip()]

# ── CAMERA ────────────────────────────────────────────────────
CAMERA_INDEX = int(os.environ.get("CAMERA_INDEX", "0"))
FRAME_WIDTH  = 640
FRAME_HEIGHT = 480
CAMERA_FPS   = 30

# ── RECOGNITION THRESHOLDS v9.3 ───────────────────────────────
#
# HOW LBPH CONFIDENCE WORKS:
#   dist=0   → conf=100%  (perfect clone of training image)
#   dist=50  → conf= 50%  (with margin=100)
#   dist=100 → conf=  0%  (at boundary)
#   dist>120 → REJECTED
#
# GOAL: enrolled student should score dist 15-50 → conf 50-85%
# The preprocessing fix in train.py + recognizer.py achieves this.
#
# If you still see conf 30-40% after retraining:
#   → Lower LBPH_UNKNOWN_MARGIN to 70 in .env
#   → dist=50, margin=70 → conf = 1-50/70 = 29% → still low
#   → Better solution: re-enrol with 200 images (not 40)
#
LBPH_THRESHOLD          = int(os.environ.get("LBPH_THRESHOLD",      "120"))
LBPH_UNKNOWN_MARGIN     = int(os.environ.get("LBPH_UNKNOWN_MARGIN", "100"))
DLIB_DISTANCE           = float(os.environ.get("DLIB_DISTANCE",     "0.50"))
MIN_CONFIDENCE_PCT      = float(os.environ.get("MIN_CONFIDENCE_PCT", "25"))
MIN_FACE_SIZE           = (80, 80)
CONFIRM_FRAMES_REQUIRED = int(os.environ.get("CONFIRM_FRAMES_REQUIRED", "2"))
DEDUP_WINDOW_SECONDS    = 30
REQUIRE_BOTH_ENGINES    = False
SOLO_ENGINE_CONFIDENCE  = 0.25

# Quality gate — dark skin needs low variance threshold
FACE_VARIANCE_MIN = 25
FACE_QUALITY_MIN  = 0.02

# ── TWIN ─────────────────────────────────────────────────────
TWIN_FEATURE_DIM       = 124
TWIN_MIN_CONFIDENCE    = 0.65
TWIN_SKELETON_WEIGHT   = 0.35
TWIN_IRIS_WEIGHT       = 0.30
TWIN_PERIOCULAR_WEIGHT = 0.20
TWIN_GEOMETRY_WEIGHT   = 0.15

# ── LIVENESS ─────────────────────────────────────────────────
LIVENESS_ON              = os.environ.get("LIVENESS_ON", "true").lower() == "true"
LIVENESS_THRESHOLD       = float(os.environ.get("LIVENESS_THRESHOLD", "0.28"))
SKELETON_LIVENESS_WEIGHT = 0.35
EYE_AR_THRESHOLD         = 0.22
BLINK_CONSEC_FRAMES      = 2
BLINK_REQUIRED           = False

# ── ENROLMENT ─────────────────────────────────────────────────
SAMPLES_PER_PERSON = 200
AUGMENT            = True

# ── CLAHE ─────────────────────────────────────────────────────
CLAHE_LIMIT = 2.0   # v9.3: lighter CLAHE — matches training preprocessing
CLAHE_GRID  = (8, 8)

# ── ATTENDANCE ────────────────────────────────────────────────
SNAPSHOTS_PER_PERIOD  = 5
MIN_PRESENT_SNAPSHOTS = 2

DEFAULT_PERIODS = [
    {"name": "Period_1", "start": "08:55", "end": "09:45"},
    {"name": "Period_2", "start": "09:45", "end": "10:35"},
    # BREAK 10:35 – 10:55
    {"name": "Period_3", "start": "10:55", "end": "11:45"},
    {"name": "Period_4", "start": "11:45", "end": "12:35"},
    # LUNCH 12:35 – 13:35
    {"name": "Period_5", "start": "13:35", "end": "14:25"},
    {"name": "Period_6", "start": "14:25", "end": "15:15"},
    {"name": "Period_7", "start": "15:15", "end": "16:05"},
]

SMS_ENABLED   = os.environ.get("SMS_ENABLED",   "false").lower() == "true"
EMAIL_ENABLED = os.environ.get("EMAIL_ENABLED", "false").lower() == "true"
OS_NAME       = platform.system()

def init_dirs():
    for _d in [DATASET_DIR, KNOWN_FACES_DIR, MODEL_DIR,
               ATTENDANCE_DIR, LOG_DIR, STATIC_DIR, FRONTEND_DIR,
               os.path.join(BASE_DIR, "data"),
               STAFF_DATASET_DIR, HOD_DATASET_DIR,
               STAFF_FACES_DIR, HOD_FACES_DIR]:
        os.makedirs(_d, exist_ok=True)


# ── F-03: admin password bcrypt detection ─────────────────────
def is_bcrypt(s: str) -> bool:
    """Return True if *s* is a bcrypt hash (starts with '$2b$' or '$2a$').

    Used by api.py _check_admin_password() and auth_routes.py to decide
    whether config.ADMIN_PASSWORD holds a plain-text value (legacy .env)
    or an already-hashed value written by a previous password reset.
    """
    return isinstance(s, str) and s.startswith(("$2b$", "$2a$"))
