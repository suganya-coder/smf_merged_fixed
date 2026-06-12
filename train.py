# =============================================================
# train.py  —  Smart Attendance System  v10.0
#
# KEY FIX v10.0 — Staff and HOD images were being skipped:
#
# PROBLEM (v9.3):
#   train_lbph() listed ALL subdirs inside data/dataset/ —
#   including "staff" and "hod" (the parent folders).
#   It then looked for .jpg files directly inside data/dataset/staff/
#   and data/dataset/hod/ — but images live one level deeper:
#     data/dataset/staff/FAC001/FAC001_p0035.jpg
#     data/dataset/hod/HOD001/HOD001_p0000.jpg
#   So it found 0 images and printed "WARN: No images — skipping".
#
# FIX (v10.0):
#   Three separate scanners, each with the correct path:
#
#   students_path = data/dataset/           (folders starting with STU_)
#   staff_path    = data/dataset/staff/     (all sub-folders inside)
#   hod_path      = data/dataset/hod/       (all sub-folders inside)
#
#   All three role groups are collected into one combined LBPH
#   model so recognition works in a single pass at runtime.
#
# PREPROCESSING (unchanged from v9.3):
#   equalizeHist only — parameter-free, matches recognizer.py.
#
# AUGMENTATION (unchanged from v9.3):
#   Covers brightness, rotation, blur, noise, zoom variants.
# =============================================================

import cv2
import os
import pickle
import numpy as np
import logging
import json
from datetime import datetime

import config

log = logging.getLogger(__name__)

try:
    import face_recognition as fr
    DLIB_OK = True
except ImportError:
    DLIB_OK = False

UNKNOWN_CLASS_ID = "__UNKNOWN__"


# =============================================================
# Preprocessing  (MUST match recognizer.py exactly)
# =============================================================
def preprocess_for_lbph(gray: np.ndarray, size: int = 160) -> np.ndarray:
    """
    v9.3 CRITICAL FIX: Use equalizeHist ONLY.
    Parameter-free -> always identical output for identical input.
    Must match _make_variants() in recognizer.py.
    """
    resized = cv2.resize(gray, (size, size))
    return cv2.equalizeHist(resized)


# =============================================================
# Augmentation
# =============================================================
def augment(gray: np.ndarray):
    """
    v9.3 augmentation — covers all realistic lighting conditions.
    Extra brightness variants are critical for dark skin.
    """
    h, w = gray.shape
    out  = []

    out.append(cv2.flip(gray, 1))

    for alpha, beta in [
        (2.2,  70), (1.8,  50), (1.5,  30), (1.3,  15), (1.1,   5),
        (0.85, -10), (0.70, -20), (0.55, -30), (0.40, -40),
    ]:
        out.append(cv2.convertScaleAbs(gray, alpha=alpha, beta=beta))

    for gamma in [1.3, 1.6, 2.0, 2.4, 2.8]:
        tbl = np.array([min(255, int(((i/255.0)**(1.0/gamma))*255))
                        for i in range(256)], np.uint8)
        out.append(cv2.LUT(gray, tbl))

    for angle in [-15, -10, -5, 5, 10, 15]:
        M = cv2.getRotationMatrix2D((w/2, h/2), angle, 1.0)
        out.append(cv2.warpAffine(gray, M, (w, h),
                                   borderMode=cv2.BORDER_REPLICATE))

    out.append(cv2.GaussianBlur(gray, (5, 5), 1.5))
    out.append(cv2.GaussianBlur(gray, (3, 3), 0.8))

    noise = np.random.normal(0, 10, gray.shape).astype(np.int16)
    out.append(np.clip(gray.astype(np.int16) + noise, 0, 255).astype(np.uint8))

    k = np.array([[-1,-1,-1],[-1,9,-1],[-1,-1,-1]], np.float32)
    out.append(np.clip(cv2.filter2D(gray, -1, k), 0, 255).astype(np.uint8))

    pad = int(h * 0.08)
    if pad > 2:
        out.append(cv2.resize(gray[pad:h-pad, pad:w-pad], (w, h)))

    noisy = gray.copy()
    n = int(0.005 * gray.size)
    coords = [np.random.randint(0, i, n) for i in gray.shape]
    noisy[coords[0], coords[1]] = 255
    coords = [np.random.randint(0, i, n) for i in gray.shape]
    noisy[coords[0], coords[1]] = 0
    out.append(noisy)

    out.append(cv2.equalizeHist(gray))

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    out.append(clahe.apply(gray))

    return out


# =============================================================
# Negative class samples
# =============================================================
def _get_negative_samples(n=400):
    """Synthetic unknown-face samples — prevents mis-labelling strangers."""
    samples = []
    rng = np.random.RandomState(42)

    for i in range(n):
        base_val = rng.randint(25, 210)
        img = np.full((160, 160), base_val, dtype=np.uint8)
        cx, cy = 80, 80
        for yy in range(0, 160, 2):
            for xx in range(0, 160, 2):
                dx = (xx - cx) / 56.0
                dy = (yy - cy) / 72.0
                if dx*dx + dy*dy < 1.0:
                    v = np.clip(base_val + rng.randint(-40, 40), 10, 245)
                    img[yy:min(160, yy+2), xx:min(160, xx+2)] = v
        noise = rng.normal(0, 18, img.shape).astype(np.int16)
        img   = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        img   = cv2.equalizeHist(img)
        samples.append(img)

    for i in range(80):
        img = rng.randint(50, 210, (160, 160), dtype=np.uint8)
        img = cv2.GaussianBlur(img.astype(np.uint8), (5, 5), 0)
        img = cv2.equalizeHist(img)
        samples.append(img)

    print(f"  Generated {len(samples)} negative class samples")
    return samples


# =============================================================
# Dataset collector  (role-aware, correct depth)
# =============================================================
def _collect_persons(role_label, base_dir, filter_prefix=None):
    """
    Scan base_dir for person sub-folders containing images.

    Returns list of (person_id, folder_path, image_filenames).

    filter_prefix: if set, only include folders whose name starts
                   with this string (e.g. "STU_" for students so
                   that "staff" and "hod" dirs are ignored when
                   scanning data/dataset/).
    """
    if not os.path.isdir(base_dir):
        print(f"  [{role_label}] Directory not found: {base_dir} — skipping")
        return []

    result = []
    for name in sorted(os.listdir(base_dir)):
        if filter_prefix and not name.upper().startswith(filter_prefix.upper()):
            continue
        full_path = os.path.join(base_dir, name)
        if not os.path.isdir(full_path):
            continue
        imgs = [f for f in os.listdir(full_path)
                if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        if imgs:
            result.append((name, full_path, imgs))
        else:
            print(f"  [{role_label}] WARN: No images for {name} — skipping")
    return result


# =============================================================
# Unified LBPH trainer  (students + staff + HOD)
# =============================================================
def train_lbph():
    print("\n--- LBPH Training v10.0 ---")
    print("  Scanning dataset paths:")
    print(f"    Students : {config.DATASET_DIR}  (STU_* folders only)")
    print(f"    Faculty  : {config.STAFF_DATASET_DIR}")
    print(f"    HOD      : {config.HOD_DATASET_DIR}")

    # ── Collect all three role groups ─────────────────────────
    #
    # KEY FIX: Students are in data/dataset/STU_xxx/
    #          We use filter_prefix="STU_" so the "staff" and "hod"
    #          sub-directories that also live in data/dataset/ are
    #          NOT treated as person folders.
    #
    #          Staff images are in data/dataset/staff/<id>/
    #          HOD   images are in data/dataset/hod/<id>/
    #          These are scanned one level deeper via their own dirs.

    student_persons = _collect_persons(
        "Student", config.DATASET_DIR, filter_prefix="STU_")

    staff_persons = _collect_persons(
        "Faculty", config.STAFF_DATASET_DIR, filter_prefix=None)

    hod_persons = _collect_persons(
        "HOD", config.HOD_DATASET_DIR, filter_prefix=None)

    all_persons = student_persons + staff_persons + hod_persons

    if not all_persons:
        print("\n  ERROR: No enrolled persons found across any role.")
        print("  Run option [1] to enroll at least one Student / Faculty / HOD.")
        return {}

    print(f"\n  Found:")
    print(f"    Students : {len(student_persons)}")
    print(f"    Faculty  : {len(staff_persons)}")
    print(f"    HOD      : {len(hod_persons)}")
    print(f"    Total    : {len(all_persons)} person(s)\n")

    # ── Load images, preprocess, augment ──────────────────────
    faces, labels, label_map, cid = [], [], {}, 0

    for pid, ppath, imgs in all_persons:
        label_map[cid] = pid
        raw = aug = 0

        for fname in imgs:
            fpath = os.path.join(ppath, fname)
            img = cv2.imread(fpath, cv2.IMREAD_GRAYSCALE)
            if img is None:
                log.warning("Could not read: %s", fpath)
                continue

            p = preprocess_for_lbph(img)
            faces.append(p)
            labels.append(cid)
            raw += 1

            if config.AUGMENT:
                for a in augment(p):
                    faces.append(a)
                    labels.append(cid)
                    aug += 1

        if raw > 0:
            print(f"  {pid}: {raw} raw + {aug} augmented = {raw+aug} total")
        else:
            print(f"  WARN: All images unreadable for {pid} — removing label")
            del label_map[cid]
            cid -= 1

        cid += 1

    if not faces:
        print("  ERROR: No images loaded — check dataset directories.")
        return {}

    # ── Negative (unknown) class ───────────────────────────────
    unknown_label            = cid
    label_map[unknown_label] = UNKNOWN_CLASS_ID
    neg_samples              = _get_negative_samples(400)
    for ns in neg_samples:
        faces.append(ns)
        labels.append(unknown_label)
    print(f"  Unknown class: {len(neg_samples)} samples (label={unknown_label})")

    # ── Train ──────────────────────────────────────────────────
    total = len(faces)
    print(f"\n  Training LBPH on {total} total samples...")

    rec = cv2.face.LBPHFaceRecognizer_create(
        radius=1, neighbors=8, grid_x=8, grid_y=8)
    rec.train(faces, np.array(labels))

    # ── Save ───────────────────────────────────────────────────
    os.makedirs(config.MODEL_DIR, exist_ok=True)
    rec.save(config.LBPH_MODEL)

    with open(config.LBPH_LABELS, "wb") as f:
        pickle.dump(label_map, f)

    with open(os.path.join(config.MODEL_DIR, "lbph_meta.json"), "w") as f:
        json.dump({"unknown_label": unknown_label}, f)

    real_count = len([v for v in label_map.values() if v != UNKNOWN_CLASS_ID])
    print(f"\n  Model saved  -> {config.LBPH_MODEL}")
    print(f"  Persons in model : {real_count}")
    print(f"  Threshold : {config.LBPH_THRESHOLD}  Margin : {config.LBPH_UNKNOWN_MARGIN}")

    # ── Self-test ──────────────────────────────────────────────
    print("\n  Self-test (expected dist < 50 for good lighting):")
    pid_to_path = {pid: (ppath, imgs) for pid, ppath, imgs in all_persons}

    for cid_t, pid_t in label_map.items():
        if pid_t == UNKNOWN_CLASS_ID:
            continue
        if pid_t not in pid_to_path:
            continue
        ppath, imgs = pid_to_path[pid_t]
        img = cv2.imread(os.path.join(ppath, imgs[0]), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            p      = preprocess_for_lbph(img)
            lb, lr = rec.predict(p)
            pred   = label_map.get(lb, "?")
            margin = config.LBPH_UNKNOWN_MARGIN
            if pred == UNKNOWN_CLASS_ID:
                status = "FAIL -- __UNKNOWN__ won (re-enrol in better light)"
            elif lr < 20:
                status = "EXCELLENT (expected conf ~80%+ at runtime)"
            elif lr < 40:
                status = "GOOD (expected conf ~60-80% at runtime)"
            elif lr < margin * 0.6:
                status = "OK (expected conf ~40-60% at runtime)"
            elif lr < margin:
                status = "PASS (expected conf ~30% -- re-enrol for better)"
            else:
                status = f"WARN dist={lr:.0f} > margin={margin} (will show Unknown)"
            print(f"    {pid_t}: pred={pred}  dist={lr:.1f}  -> {status}")

    return label_map


# =============================================================
# dlib encoding trainer  (students + staff + HOD color images)
# =============================================================
def train_dlib():
    print("\n--- dlib Encoding Training ---")
    if not DLIB_OK:
        print("  Skipped -- face_recognition not installed")
        return
    if not os.path.isdir(config.KNOWN_FACES_DIR):
        print("  known_faces/ not found -- skipped")
        return

    def _iter_known(base, prefix_filter=None):
        if not os.path.isdir(base):
            return
        for name in sorted(os.listdir(base)):
            if prefix_filter and not name.upper().startswith(prefix_filter.upper()):
                continue
            p = os.path.join(base, name)
            if os.path.isdir(p):
                yield name, p

    # Students (STU_ prefix), then staff/*, then hod/*
    person_dirs  = list(_iter_known(config.KNOWN_FACES_DIR, prefix_filter="STU_"))
    person_dirs += list(_iter_known(config.STAFF_FACES_DIR))
    person_dirs += list(_iter_known(config.HOD_FACES_DIR))

    if not person_dirs:
        print("  No persons in known_faces/ -- skipped")
        return

    db_enc = {}
    for pid, ppath in person_dirs:
        imgs = [f for f in os.listdir(ppath)
                if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        if not imgs:
            continue

        encs = []
        for fname in imgs[:100]:
            try:
                img = fr.load_image_file(os.path.join(ppath, fname))
                mean = np.mean(img)
                if mean < 80:
                    boost = int((80 - mean) * 0.8)
                    img   = np.clip(img.astype(np.int32) + boost,
                                    0, 255).astype(np.uint8)
                locs = fr.face_locations(img, model="hog")
                if not locs:
                    locs = fr.face_locations(
                        img, model="hog", number_of_times_to_upsample=2)
                if locs:
                    enc = fr.face_encodings(
                        img, locs[:1], num_jitters=2, model="large")
                    if enc:
                        encs.append(enc[0])
            except Exception:
                pass

        if encs:
            db_enc[pid] = encs
            print(f"  {pid}: {len(encs)} encodings")
        else:
            print(f"  WARN: 0 encodings for {pid} (check known_faces/{pid}/)")

    if db_enc:
        with open(config.DLIB_ENCODINGS, "wb") as f:
            pickle.dump(db_enc, f)
        print(f"  Saved {len(db_enc)} person(s) to dlib encodings")


# =============================================================
# Entry point
# =============================================================
def train_all():
    print("\n" + "="*62)
    print(f"  Smart Attendance v10.0 -- Training")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*62)

    lm = train_lbph()
    train_dlib()

    try:
        from twin_analysis import train_twin_model
        train_twin_model()
    except Exception as e:
        log.debug("Twin train: %s", e)

    real = len([v for v in lm.values() if v != UNKNOWN_CLASS_ID])

    print("\n" + "="*62)
    print(f"  Training COMPLETE -- {real} enrolled person(s) in model")
    print(f"\n  ACCURACY TIPS:")
    print(f"  * If conf < 60%: Re-enrol with more images (200+)")
    print(f"    and better lighting on face")
    print(f"  * Press D in camera window to see exact LBPH distances")
    print(f"  * dist < 40 = conf > 60% = green box")
    print(f"  * dist < 20 = conf > 80% = bright green box")
    print("="*62 + "\n")
    return lm


if __name__ == "__main__":
    train_all()
