# =============================================================
# enroll.py  —  Smart Attendance System  v9.2
#
# UPDATED FIELDS (v9.2):
#  1. Register_Number   6. Date_of_Birth   11. Student_Email
#  2. Roll_Number       7. Department      12. Parent_Email
#  3. First_Name        8. Course          13. Student_Mobile
#  4. Last_Name         9. Year            14. Parent_Mobile
#  5. Gender           10. Section         15. Status (auto=Active)
#
# CAMERA FIXES:
#  1. Uses cv2.CAP_DSHOW backend on Windows (avoids RGB24 error)
#  2. Falls back through MSMF -> AUTO if DSHOW fails
#  3. Camera opens at 640x480 (safe) not 1280x720 (broken)
#  4. Read-timeout recovery: skips bad frames, never freezes
#  5. Removed Unicode chars that crash Windows consoles
#  6. cap.set() called AFTER open to avoid format lock
# =============================================================
import cv2
import os
import time
import logging
import numpy as np
import config
import database as db
import lighting

log = logging.getLogger(__name__)


# =============================================================
# Camera helpers
# =============================================================
def _open_camera(index: int):
    import platform
    if platform.system() == "Windows":
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    else:
        backends = [cv2.CAP_ANY, cv2.CAP_V4L2]

    for backend in backends:
        try:
            cap = cv2.VideoCapture(index + (backend if backend != cv2.CAP_ANY else 0))
            if backend != cv2.CAP_ANY:
                cap.release()
                cap = cv2.VideoCapture(index, backend)

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
                log.info("Camera opened backend=%s %dx%d", backend, w, h)
                print(f"  Camera backend: {backend}  resolution: {w}x{h}")
                return cap
            cap.release()
        except Exception as e:
            log.debug("Backend %s failed: %s", backend, e)

    return None


def _read_frame(cap, timeout_s: float = 2.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ret, frame = cap.read()
        if ret and frame is not None and frame.size > 0:
            return True, frame
        time.sleep(0.02)
    return False, None


# =============================================================
# Quality check
# =============================================================
def _quality_ok(gray_face: np.ndarray) -> tuple:
    if gray_face is None or gray_face.size == 0:
        return False, "no face"
    h, w = gray_face.shape[:2]
    if min(h, w) < 35:
        return False, "too small"
    blur = float(cv2.Laplacian(gray_face, cv2.CV_64F).var())
    if blur < 12:
        return False, f"blurry {blur:.0f}"
    mean_br = float(np.mean(gray_face))
    if mean_br < 8:
        return False, f"dark {mean_br:.0f}"
    if mean_br > 248:
        return False, f"overexposed {mean_br:.0f}"
    return True, "ok"


# =============================================================
# UI overlay
# =============================================================
def _draw_ui(frame, pose_label, hint, count, target, face_rect,
             brightness, blur, recording):
    H, W = frame.shape[:2]
    out  = frame.copy()

    cv2.rectangle(out, (0, 0), (W, 65), (25, 25, 25), -1)
    cv2.putText(out, pose_label, (10, 26),
                cv2.FONT_HERSHEY_DUPLEX, 0.8, (0, 210, 255), 1)
    cv2.putText(out, hint, (10, 54),
                cv2.FONT_HERSHEY_SIMPLEX, 0.46, (190, 190, 190), 1)

    if target > 0:
        pct   = min(count / float(target), 1.0)
        bar_w = W - 20
        cv2.rectangle(out, (10, H-30), (10+bar_w, H-8), (50,50,50), -1)
        cv2.rectangle(out, (10, H-30), (10+int(bar_w*pct), H-8),
                      (0, 200, 80), -1)
        cv2.putText(out, f"Saved {count}/{target}", (14, H-34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220,220,220), 1)

    if face_rect is not None:
        x, y, w, h = face_rect
        col = ((0,200,80) if blur > 60 else
               (0,165,255) if blur > 25 else (0,0,210))
        cv2.rectangle(out, (x, y), (x+w, y+h), col, 2)
        cv2.putText(out, f"blur={blur:.0f} br={brightness:.0f}",
                    (x, y-6), cv2.FONT_HERSHEY_SIMPLEX, 0.40,
                    (255,255,255), 1)

    if recording:
        status = f"Recording ({count}/{target}) -- ESC=stop"
        col_s  = (0, 255, 120)
    else:
        status = "SPACE = start recording   ESC = cancel"
        col_s  = (180, 180, 180)

    if brightness < 55:
        light_msg = "! Too dark -- move to better light"
        light_col = (0, 60, 255)
    elif brightness < 85:
        light_msg = "Lighting: OK (brighter = better)"
        light_col = (0, 165, 255)
    else:
        light_msg = "Lighting: Good"
        light_col = (0, 200, 80)

    cv2.putText(out, light_msg, (10, H-36),
                cv2.FONT_HERSHEY_SIMPLEX, 0.44, light_col, 1)
    cv2.putText(out, status, (10, H-56),
                cv2.FONT_HERSHEY_SIMPLEX, 0.44, col_s, 1)

    return out


# =============================================================
# Collect one pose
# =============================================================
CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def collect_pose(cap, pose_label, hint, student_dir, color_dir,
                 prefix, target=40, start_index=0):
    win = "Enrolment"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, 720, 540)

    recording  = False
    count      = start_index   # start from offset so filenames are unique across poses
    attempts   = 0
    max_att    = target * 12
    consecutive_fail = 0

    print(f"  Window open -- press SPACE in the window to start, ESC to skip.")

    while True:
        ret = cap.grab()
        if ret:
            ret, frame = cap.retrieve()
        else:
            frame = None

        if not ret or frame is None or frame.size == 0:
            consecutive_fail += 1
            if consecutive_fail > 30:
                print("  Camera stopped sending frames. Check USB connection.")
                break
            blank = np.zeros((480, 640, 3), np.uint8)
            cv2.putText(blank, "Waiting for camera...",
                        (120, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (0, 140, 255), 2)
            cv2.imshow(win, blank)
            key = cv2.waitKey(100) & 0xFF
            if key == 27:
                break
            continue

        consecutive_fail = 0

        proc  = lighting.preprocess_frame(frame)
        gray  = cv2.cvtColor(proc, cv2.COLOR_BGR2GRAY)
        eq    = cv2.equalizeHist(gray)

        _clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8))
        cg     = _clahe.apply(gray)
        _t     = np.array([min(255, int(((i/255.0)**0.5)*255)) for i in range(256)], np.uint8)
        bright = cv2.LUT(cg, _t)
        faces  = []
        for _src, _sc, _nb in [(cg, 1.05, 2), (bright, 1.05, 2), (eq, 1.08, 3)]:
            _det = CASCADE.detectMultiScale(_src, scaleFactor=_sc,
                                             minNeighbors=_nb, minSize=(40, 40))
            if len(_det):
                faces = [tuple(r) for r in _det]
                break
        if not len(faces):
            faces = []

        face_rect  = None
        brightness = float(np.mean(gray))
        blur_val   = 0.0
        fh, fw     = frame.shape[:2]

        if len(faces):
            x, y, w, h = max(faces, key=lambda r: r[2]*r[3])
            face_rect   = (x, y, w, h)
            fg = gray[max(0,y):min(fh,y+h), max(0,x):min(fw,x+w)]
            if fg.size > 0:
                blur_val = float(cv2.Laplacian(fg, cv2.CV_64F).var())

            if recording:
                attempts += 1
                fc = proc[max(0,y):min(fh,y+h), max(0,x):min(fw,x+w)]
                good, _ = _quality_ok(fg)
                if good and fc.size > 0:
                    fname = f"{prefix}_p{count:04d}.jpg"
                    g_r   = cv2.resize(fg, (160, 160))
                    c_r   = cv2.resize(fc, (160, 160))
                    cv2.imwrite(os.path.join(student_dir, fname), g_r)
                    cv2.imwrite(os.path.join(color_dir,   fname), c_r)
                    count += 1
                    if count >= start_index + target:
                        break
                if attempts >= max_att:
                    print(f"  Max attempts reached. Saved {count - start_index}/{target}.")
                    break

        saved_this_pose = count - start_index
        display = _draw_ui(frame, pose_label, hint, saved_this_pose, target,
                           face_rect, brightness, blur_val, recording)
        cv2.imshow(win, display)

        key = cv2.waitKey(1) & 0xFF

        if key == 27:
            print("  Pose skipped.")
            break
        if key == 32 and not recording:
            recording = True
            print(f"  Recording... (target: {target} images)")

    cv2.destroyWindow(win)
    for _ in range(5):
        cv2.waitKey(1)

    saved_this_pose = count - start_index
    print(f"  Pose done: {saved_this_pose}/{target} images saved.")
    return saved_this_pose


# =============================================================
# POSES
# =============================================================
POSES = [
    ("1/5  LOOK STRAIGHT",   "Face camera directly, chin level"),
    ("2/5  TURN HEAD LEFT",  "Slowly turn head left ~20 degrees"),
    ("3/5  TURN HEAD RIGHT", "Slowly turn head right ~20 degrees"),
    ("4/5  TILT HEAD UP",    "Tilt chin slightly upward"),
    ("5/5  TILT HEAD DOWN",  "Lower chin slightly toward chest"),
]


# =============================================================
# Input helpers
# =============================================================
def _prompt(label, required=False, default=""):
    while True:
        val = input(f"  {label}: ").strip()
        if val:
            return val
        if not required:
            return default
        print(f"  ERROR: {label.split('(')[0].strip()} is required.")


def _prompt_choice(label, choices):
    choices_str = " / ".join(choices)
    while True:
        val = input(f"  {label} ({choices_str}): ").strip().upper()
        if val in [c.upper() for c in choices]:
            # Return the properly cased version
            for c in choices:
                if c.upper() == val:
                    return c
        print(f"  Please enter one of: {choices_str}")


# =============================================================
# Main entry point
# =============================================================
def enroll_student():
    print("\n" + "=" * 55)
    print("  Student Enrolment  v9.2")
    print("=" * 55)
    print("  LIGHTING TIP: Face a window or lamp -- not your back to it.")
    print("  For dark skin: extra light on face greatly helps accuracy.\n")

    # ── Collect all 15 fields ─────────────────────────────────
    register_number = _prompt("Register Number (unique)", required=True)
    roll_number     = _prompt("Roll Number", required=True)
    first_name      = _prompt("First Name", required=True)
    last_name       = _prompt("Last Name",  required=True)
    gender          = _prompt_choice("Gender", ["Male", "Female", "Other"])
    dob             = _prompt("Date of Birth (YYYY-MM-DD)", required=False)
    department      = _prompt("Department (e.g. CSE / ECE / IT)", required=False)
    course          = _prompt("Course (e.g. B.E / B.Tech / B.Sc)", required=False)
    year            = _prompt_choice("Year", ["1st Year", "2nd Year", "3rd Year", "4th Year"])
    section         = _prompt("Section (A/B/C)", required=False, default="A").upper()
    student_email   = _prompt("Student Email (Gmail)", required=False)
    parent_email    = _prompt("Parent  Email (Gmail)", required=False)
    student_mobile  = _prompt("Student Mobile", required=False)
    parent_mobile   = _prompt("Parent  Mobile", required=False)
    twin_of         = _prompt("Twin of (Student ID or blank)", required=False) or None

    full_name = f"{first_name} {last_name}".strip()

    sid = f"STU_{register_number.upper()}"
    print(f"\n  Student ID : {sid}")
    print(f"  Full Name  : {full_name}")
    print(f"  Dept/Year  : {department} | {year} | Section {section}")
    print(f"  Status     : Active\n")

    # ── Directories ───────────────────────────────────────────
    gray_dir  = os.path.join(config.DATASET_DIR,     sid)
    color_dir = os.path.join(config.KNOWN_FACES_DIR, sid)
    os.makedirs(gray_dir,  exist_ok=True)
    os.makedirs(color_dir, exist_ok=True)

    # ── DB insert / check ─────────────────────────────────────
    ok = db.add_student(
        student_id      = sid,
        name            = full_name,
        roll_number     = roll_number,
        register_number = register_number,
        first_name      = first_name,
        last_name       = last_name,
        gender          = gender,
        date_of_birth   = dob,
        department      = department,
        course          = course,
        year            = year,
        section         = section,
        student_email   = student_email,
        parent_email    = parent_email,
        student_mobile  = student_mobile,
        parent_mobile   = parent_mobile,
        status          = "Active",
        twin_of         = twin_of,
    )

    if not ok:
        print("\n  WARNING: This student data already exists in the database.")
        choice = input("  Do you want to train again? (Y/N): ").strip().upper()
        if choice != "Y":
            print("  Enrolment cancelled. Camera will not open.")
            return
        print("  Re-training existing student images...")

    # ── Open camera ───────────────────────────────────────────
    print("\n  Opening camera...")
    cap = _open_camera(config.CAMERA_INDEX)
    if cap is None:
        print("  ERROR: Could not open camera with any backend.")
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

    total = 0
    for i, (pose_name, hint) in enumerate(POSES):
        print(f"\n  === Pose {i+1}/5: {pose_name} ===")
        print(f"  {hint}")
        saved = collect_pose(
            cap         = cap,
            pose_label  = f"{i+1}/5  {pose_name}",
            hint        = hint,
            student_dir = gray_dir,
            color_dir   = color_dir,
            prefix      = sid,
            target      = 40,
            start_index = total,   # unique filenames across all poses
        )
        total += saved

    cap.release()
    cv2.destroyAllWindows()

    print(f"\n  Enrolment COMPLETE: {total} images for {full_name}")
    print(f"  Student ID  : {sid}")
    print(f"  Register No : {register_number}")
    print(f"  Department  : {department}  |  Course: {course}  |  {year}")
    if total < 100:
        print(f"  WARNING: Only {total} images collected (target=200).")
        print(f"  Consider re-enrolling for better accuracy.")
    print(f"  -> Run option [2] to train models.\n")
