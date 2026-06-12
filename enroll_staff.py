# =============================================================
# enroll_staff.py  —  Smart Attendance System  v10.1
#
# Faculty Enrollment Module
# NOTE: The 'staff' table has been removed and merged into 'faculty'.
#       database.add_staff() now writes directly to the faculty table.
#
# Fields Collected:
#   Staff ID, Employee Code, First Name, Last Name, Gender,
#   Date of Birth, Department, Role=Faculty, Designation,
#   Email, Mobile Number, Joining Date
#
# Face Capture: 5 poses × up to 40 images per pose
# Dataset saved to: data/dataset/staff/{staff_id}/
# Model saved to:   models/staff_face_model.pkl
# =============================================================
import cv2
import os
import pickle
import time
import logging
import numpy as np
import config
import database as db

# Re-use shared camera/pose utilities from enroll.py
from enroll import (
    _open_camera, _read_frame, _quality_ok, _draw_ui,
    collect_pose, POSES, _prompt, _prompt_choice,
    CASCADE
)

log = logging.getLogger(__name__)


# =============================================================
# LBPH Training for Staff
# =============================================================
def _train_staff_lbph(dataset_dir: str, model_path: str, role_label: str):
    """Train LBPH model on all persons inside dataset_dir."""
    print(f"\n─── Training {role_label} Face Model ───")

    persons = sorted([
        d for d in os.listdir(dataset_dir)
        if os.path.isdir(os.path.join(dataset_dir, d))
    ])
    if not persons:
        print(f"  No {role_label} images found in {dataset_dir} — training skipped.")
        return

    faces, labels, label_map, cid = [], [], {}, 0

    for pid in persons:
        ppath = os.path.join(dataset_dir, pid)
        imgs = [f for f in os.listdir(ppath)
                if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        if not imgs:
            print(f"  WARN: No images for {pid} — skipping")
            continue

        label_map[cid] = pid
        for fname in imgs:
            img = cv2.imread(os.path.join(ppath, fname), cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            img = cv2.resize(img, (160, 160))
            img = cv2.equalizeHist(img)
            faces.append(img)
            labels.append(cid)
        print(f"  {pid}: {len(imgs)} images loaded")
        cid += 1

    if not faces:
        print(f"  ERROR: No images could be loaded for {role_label}.")
        return

    rec = cv2.face.LBPHFaceRecognizer_create(
        radius=1, neighbors=8, grid_x=8, grid_y=8)
    rec.train(faces, np.array(labels))

    os.makedirs(config.MODEL_DIR, exist_ok=True)

    # Save as .pkl (pickle wrapper around the yml bytes)
    import tempfile, shutil
    tmp_yml = model_path.replace(".pkl", "_tmp.yml")
    rec.save(tmp_yml)
    with open(tmp_yml, "rb") as f:
        yml_bytes = f.read()
    os.remove(tmp_yml)

    with open(model_path, "wb") as f:
        pickle.dump({"label_map": label_map, "yml_bytes": yml_bytes}, f)

    print(f"  {role_label} model saved → {model_path}")
    print(f"  Persons trained: {list(label_map.values())}")


# =============================================================
# Main entry point
# =============================================================
def enroll_faculty():
    print("\n" + "=" * 55)
    print("  Faculty Enrollment  v10.0")
    print("=" * 55)
    print("  LIGHTING TIP: Face a window or lamp -- not your back to it.\n")

    # ── Collect fields ─────────────────────────────────────────
    staff_id       = _prompt("Staff ID (unique, e.g. FAC001)", required=True).upper()
    employee_code  = _prompt("Employee Code", required=False)
    first_name     = _prompt("First Name", required=True)
    last_name      = _prompt("Last Name",  required=True)
    gender         = _prompt_choice("Gender", ["Male", "Female", "Other"])
    dob            = _prompt("Date of Birth (YYYY-MM-DD)", required=False)
    department     = _prompt("Department (e.g. CSE / ECE / IT)", required=False)
    designation    = _prompt("Designation (e.g. Assistant Professor)", required=False)
    email          = _prompt("Email (Gmail)", required=False)
    mobile         = _prompt("Mobile Number", required=False)
    joining_date   = _prompt("Joining Date (YYYY-MM-DD)", required=False)

    full_name = f"{first_name} {last_name}".strip()

    print(f"\n  Staff ID   : {staff_id}")
    print(f"  Full Name  : {full_name}")
    print(f"  Department : {department}")
    print(f"  Role       : Faculty\n")

    # ── Dataset directories ────────────────────────────────────
    gray_dir  = os.path.join(config.STAFF_DATASET_DIR,  staff_id)
    color_dir = os.path.join(config.STAFF_FACES_DIR, staff_id)
    os.makedirs(gray_dir,  exist_ok=True)
    os.makedirs(color_dir, exist_ok=True)

    # ── DB insert — writes to unified faculty table ────────────
    # (staff table removed; add_staff() redirects to faculty)
    ok = db.add_staff(
        staff_id      = staff_id,
        employee_code = employee_code,
        first_name    = first_name,
        last_name     = last_name,
        gender        = gender,
        date_of_birth = dob,
        department    = department,
        designation   = designation,
        email         = email,
        mobile        = mobile,
        joining_date  = joining_date,
    )

    if not ok:
        print("\n  WARNING: This staff record already exists in the database.")
        choice = input("  Do you want to train again? (Y/N): ").strip().upper()
        if choice != "Y":
            print("  Enrollment cancelled. Camera will not open.")
            return
        print("  Re-training existing staff face images...")

    # ── Open camera ───────────────────────────────────────────
    print("\n  Opening camera...")
    cap = _open_camera(config.CAMERA_INDEX)
    if cap is None:
        print("  ERROR: Could not open camera.")
        print("  Solutions:")
        print("    1. Close any app using the camera (Teams, Zoom, etc.)")
        print("    2. Set CAMERA_INDEX=1 in .env if you have two cameras")
        print("    3. Restart your PC and try again")
        return

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"  Camera ready: {w}x{h}")
    print(f"  INSTRUCTIONS: A window will open for each pose.")
    print(f"  Press SPACE to start recording, ESC to skip a pose.\n")

    # ── Capture 5 poses ───────────────────────────────────────
    STAFF_POSES = [
        ("1/5  FRONT FACE",    "Look directly at the camera"),
        ("2/5  SLIGHT LEFT",   "Turn head slightly to the left (~15 degrees)"),
        ("3/5  SLIGHT RIGHT",  "Turn head slightly to the right (~15 degrees)"),
        ("4/5  LOOK UP",       "Tilt chin slightly upward"),
        ("5/5  NATURAL FACE",  "Relax, natural expression, face camera"),
    ]

    total = 0
    for i, (pose_name, hint) in enumerate(STAFF_POSES):
        print(f"\n  === Pose {i+1}/5: {pose_name} ===")
        print(f"  {hint}")
        saved = collect_pose(
            cap         = cap,
            pose_label  = pose_name,
            hint        = hint,
            student_dir = gray_dir,
            color_dir   = color_dir,
            prefix      = staff_id,
            target      = 40,
        )
        total += saved

    cap.release()
    cv2.destroyAllWindows()

    # ── Train model ───────────────────────────────────────────
    print(f"\n  Training staff face recognition model...")
    _train_staff_lbph(
        dataset_dir = config.STAFF_DATASET_DIR,
        model_path  = config.STAFF_LBPH_MODEL,
        role_label  = "Faculty",
    )

    # ── Summary ───────────────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"  Faculty Enrollment COMPLETE")
    print(f"{'='*55}")
    print(f"  Staff ID      : {staff_id}")
    print(f"  Employee Code : {employee_code}")
    print(f"  Full Name     : {full_name}")
    print(f"  Department    : {department}")
    print(f"  Role          : Faculty")
    print(f"  Designation   : {designation}")
    print(f"  Email         : {email}")
    print(f"  Mobile        : {mobile}")
    print(f"  Joining Date  : {joining_date}")
    print(f"  Images Saved  : {total}")
    print(f"  Dataset Path  : data/dataset/staff/{staff_id}/")
    print(f"  Model Saved   : models/staff_face_model.pkl")
    if total < 100:
        print(f"\n  WARNING: Only {total} images collected (target=200).")
        print(f"  Consider re-enrolling for better accuracy.")
    print(f"{'='*55}\n")
