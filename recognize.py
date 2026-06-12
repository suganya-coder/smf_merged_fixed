# =============================================================
# recognize.py  —  Smart Attendance System
#
# Standalone face recognition script using LBPH + Haar Cascade.
# Merges recognizer.py (v9.3) and recognizer1.py (v9.4) into a
# single clean file — no external config/lighting/database deps.
#
# Usage:
#   python recognize.py
#
# Controls:
#   Q  — Quit
#
# Requirements:
#   pip install opencv-contrib-python numpy
#
# Expected project layout:
#   models/
#     lbph_model.yml
#     lbph_labels.pkl
# =============================================================

import cv2
import pickle
import numpy as np
from collections import deque, Counter

# ── Configuration ─────────────────────────────────────────────────────────────

MODEL_PATH  = "models/lbph_model.yml"   # Trained LBPH model
LABELS_PATH = "models/lbph_labels.pkl"  # {int_label: "StudentID"} mapping

# LBPH distance thresholds.
# dist=0   → perfect match  → confidence=100%
# dist=MAX → worst match    → confidence=0%
#
# LBPH_UNKNOWN_MARGIN: hard acceptance gate.
#   Faces with dist >= this value are rejected as Unknown.
#   Lower = stricter. Typical range: 55–100.
#
# LBPH_SINGLE_ENROLL_CAP: tighter cap used when only ONE person is enrolled.
#   With a single enrolled person, LBPH always returns that label regardless
#   of who is in frame. This cap prevents false-positive matches.
LBPH_UNKNOWN_MARGIN     = 100
LBPH_SINGLE_ENROLL_CAP = 55

# Minimum LBPH confidence (0.0–1.0) to show a name on screen.
# Below this threshold the face is displayed as "Unknown".
MIN_DISPLAY_CONFIDENCE = 0.45

# Confirmation buffer: how many video frames must consistently agree on
# the same identity before it is treated as confirmed.
# Higher = more stable but slower to respond. Recommended: 5–8.
CONFIRM_FRAMES_REQUIRED = 6

# Face detection parameters.
HAAR_SCALE_FACTOR  = 1.10  # How much image is reduced each pass (1.05–1.3)
HAAR_MIN_NEIGHBORS = 5     # Minimum neighbour rectangles to keep a face
HAAR_MIN_FACE_SIZE = (80, 80)  # Minimum face size in pixels

# ── Haar Cascade ──────────────────────────────────────────────────────────────

_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

# ── Preprocessing ─────────────────────────────────────────────────────────────

def _preprocess_face(gray: np.ndarray) -> np.ndarray:
    """
    Resize to 160×160 and apply equalizeHist.

    This MUST match the preprocessing used during training (train.py).
    equalizeHist is parameter-free — identical output every run —
    which keeps LBPH distances low (5–30) for known faces.
    """
    resized = cv2.resize(gray, (160, 160))
    return cv2.equalizeHist(resized)


def _make_variants(gray: np.ndarray) -> list:
    """
    Generate multiple preprocessing variants of the face crop.

    The primary variant (equalizeHist) matches the training pipeline,
    so it will typically yield the best distance. The secondary variants
    act as fallbacks for different lighting conditions (dark rooms,
    bright sunlight, motion blur, dark skin tones).

    Returns a list of preprocessed 160×160 grayscale images.
    """
    base = cv2.resize(gray, (160, 160))
    variants = []

    # 0 — PRIMARY: equalizeHist — matches training pipeline
    eq = cv2.equalizeHist(base)
    variants.append(eq)

    # 1 — Light CLAHE
    clahe_light = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    variants.append(clahe_light.apply(base))

    # 2 — Strong CLAHE
    clahe_strong = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8))
    variants.append(clahe_strong.apply(base))

    # 3–5 — Gamma-brightened equalised (helps with dark skin under dim light)
    for gamma in [1.4, 1.8, 2.2]:
        table = np.array(
            [min(255, int(((i / 255.0) ** (1.0 / gamma)) * 255))
             for i in range(256)],
            dtype=np.uint8
        )
        variants.append(cv2.LUT(eq, table))

    # 6 — Histogram stretched then equalised
    mn, mx = float(base.min()), float(base.max())
    if mx - mn > 10:
        stretched = np.clip(
            (base.astype(np.float32) - mn) / (mx - mn) * 255,
            0, 255
        ).astype(np.uint8)
        variants.append(cv2.equalizeHist(stretched))

    # 7 — Sharpened (helps with soft-focus / slightly blurry frames)
    kernel = np.array([[-1, -1, -1],
                       [-1,  9, -1],
                       [-1, -1, -1]], dtype=np.float32)
    variants.append(
        np.clip(cv2.filter2D(eq, -1, kernel), 0, 255).astype(np.uint8)
    )

    # 8 — Gaussian blurred (motion tolerance)
    variants.append(cv2.GaussianBlur(eq, (3, 3), 0))

    # 9 — Raw, no processing (extreme fallback)
    variants.append(base)

    return variants


# ── Confirmation Buffer ────────────────────────────────────────────────────────

class _ConfirmBuffer:
    """
    Requires N consistent frame matches before confirming an identity.

    The buffer holds the last `n` recognition results. A student ID is
    only "confirmed" once it appears in at least half the buffer AND
    in at least CONFIRM_FRAMES_REQUIRED frames. Unknown/None votes dilute
    the count, preventing instant false-positive confirmations.
    """

    def __init__(self, n: int = 12):
        self._n     = n
        self._ids   = deque(maxlen=n)
        self._confs: dict = {}

    def push(self, student_id, confidence: float) -> None:
        self._ids.append(student_id)
        if student_id:
            self._confs.setdefault(student_id, deque(maxlen=self._n))
            self._confs[student_id].append(confidence)

    def get(self):
        """
        Returns (confirmed_id, avg_confidence) if a stable identity has
        been detected, or (None, 0.0) if no consensus yet.
        """
        min_fill = max(CONFIRM_FRAMES_REQUIRED, 5)
        if len(self._ids) < min_fill:
            return None, 0.0

        counts = Counter(x for x in self._ids if x)
        if not counts:
            return None, 0.0

        best_id, best_count = counts.most_common(1)[0]

        # Winning ID must appear in at least half the total buffer so that
        # unknown frames (None) can outvote a weak or fleeting match.
        required = max(CONFIRM_FRAMES_REQUIRED, len(self._ids) // 2)
        if best_count < required:
            return None, 0.0

        confs    = list(self._confs.get(best_id, [0.0]))
        avg_conf = float(np.mean(confs)) if confs else 0.0
        return best_id, avg_conf

    def reset(self) -> None:
        self._ids.clear()
        self._confs.clear()


# ── LBPH Distance → Confidence Conversion ────────────────────────────────────

def _lbph_confidence(dist: float, margin: float) -> float:
    """
    Convert an LBPH distance to a confidence percentage (0.0–1.0).

    Formula:  confidence = 1 - (dist / margin)

    Interpretation:
      dist = 0        → confidence = 1.00  (100% — perfect match)
      dist = margin/2 → confidence = 0.50  ( 50% — borderline)
      dist = margin   → confidence = 0.00  (  0% — at rejection boundary)
      dist > margin   → face is rejected (not called)

    The margin acts as the "worst acceptable distance". Anything beyond it
    is treated as Unknown regardless of which label LBPH returns, since
    LBPH always returns the closest enrolled label even for strangers.
    """
    return float(np.clip(1.0 - dist / margin, 0.0, 1.0))


# ── Face Recognizer ───────────────────────────────────────────────────────────

class FaceRecognizer:
    """
    Self-contained LBPH face recognizer.

    Loads the trained model and label map, detects faces with Haar Cascade,
    runs multi-variant LBPH recognition, applies a confirmation buffer to
    reduce flickering, and draws results on each frame.
    """

    def __init__(self):
        self.model: cv2.face.LBPHFaceRecognizer = None  # type: ignore
        self.labels: dict = {}   # {int → "StudentID_or_Name"}
        self._confirm_buffers: dict = {}  # keyed by face-grid cell
        self._load_model()

    # ── Model Loading ──────────────────────────────────────────────────────

    def _load_model(self) -> None:
        """Load the LBPH model and label mapping from disk."""
        try:
            self.model = cv2.face.LBPHFaceRecognizer_create()
            self.model.read(MODEL_PATH)
            print(f"[OK] LBPH model loaded: {MODEL_PATH}")
        except Exception as exc:
            print(f"[ERROR] Could not load LBPH model: {exc}")
            print("        → Make sure models/lbph_model.yml exists.")
            print("        → Run your training script first.")
            self.model = None

        try:
            with open(LABELS_PATH, "rb") as fh:
                self.labels = pickle.load(fh)
            real = [v for v in self.labels.values() if v != "__UNKNOWN__"]
            print(f"[OK] Labels loaded: {real}")
        except Exception as exc:
            print(f"[ERROR] Could not load labels: {exc}")
            self.labels = {}

    # ── Face Detection ─────────────────────────────────────────────────────

    def _detect_faces(self, frame: np.ndarray) -> list:
        """
        Detect faces in a BGR frame using Haar Cascade.

        Returns a list of (x, y, w, h) bounding boxes.
        Two-pass strategy: strict pass first, relaxed fallback if nothing found.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Enhance contrast for detection (equalizeHist + mild gamma)
        eq  = cv2.equalizeHist(gray)
        table = np.array(
            [min(255, int(((i / 255.0) ** (1.0 / 1.5)) * 255))
             for i in range(256)],
            dtype=np.uint8
        )
        detect_src = cv2.LUT(eq, table)

        # Strict pass — fewer false positives
        faces = _CASCADE.detectMultiScale(
            detect_src,
            scaleFactor  = HAAR_SCALE_FACTOR,
            minNeighbors = HAAR_MIN_NEIGHBORS,
            minSize      = HAAR_MIN_FACE_SIZE,
            flags        = cv2.CASCADE_SCALE_IMAGE
        )

        # Relaxed fallback — catches faces in poor lighting
        if not len(faces):
            faces = _CASCADE.detectMultiScale(
                eq,
                scaleFactor  = HAAR_SCALE_FACTOR,
                minNeighbors = max(HAAR_MIN_NEIGHBORS - 1, 3),
                minSize      = HAAR_MIN_FACE_SIZE,
                flags        = cv2.CASCADE_SCALE_IMAGE
            )

        if not len(faces):
            return []

        return [tuple(f) for f in faces]

    # ── LBPH Recognition ───────────────────────────────────────────────────

    def _recognize_face(self, frame: np.ndarray, rect: tuple):
        """
        Run LBPH recognition on a single face crop.

        Returns (name, confidence_float) where:
          name       — person's name/ID string, or None if Unknown
          confidence — float 0.0–1.0 (converted from LBPH distance)

        Strategy:
          1. Crop and convert to grayscale.
          2. Generate multiple preprocessing variants.
          3. Run LBPH.predict() on each variant, keep the best (lowest) distance.
          4. Apply strict distance gate — reject if dist >= effective_margin.
          5. Reject __UNKNOWN__ synthetic class.
          6. Apply minimum confidence floor of MIN_DISPLAY_CONFIDENCE.
          7. Convert distance to confidence percentage.
        """
        if self.model is None or not self.labels:
            return None, 0.0

        x, y, w, h = rect
        fh, fw = frame.shape[:2]

        # Crop face region with boundary clamp
        crop = frame[max(0, y):min(fh, y + h), max(0, x):min(fw, x + w)]
        if crop is None or crop.size == 0:
            return None, 0.0

        gray_crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

        # Upscale very small faces so LBPH has enough pixels
        ch, cw = gray_crop.shape[:2]
        if min(ch, cw) < 112:
            scale = 112.0 / min(ch, cw)
            gray_crop = cv2.resize(
                gray_crop,
                (int(cw * scale), int(ch * scale)),
                interpolation=cv2.INTER_CUBIC
            )

        # Quality gate: reject walls / backgrounds (low variance)
        if np.var(gray_crop) < 25:
            return None, 0.0

        # Run LBPH on all preprocessing variants, keep best (lowest) distance
        variants  = _make_variants(gray_crop)
        best_dist = 9999.0
        best_label = None

        for variant in variants:
            try:
                label, dist = self.model.predict(variant)
                if dist < best_dist:
                    best_dist  = dist
                    best_label = label
            except Exception:
                continue

        if best_label is None:
            return None, 0.0

        # Determine strict acceptance margin.
        # When only ONE real person is enrolled, LBPH will always return that
        # label — even for a completely unknown face. Tighten the gate.
        real_count = sum(
            1 for v in self.labels.values() if v != "__UNKNOWN__"
        )
        effective_margin = (
            min(LBPH_UNKNOWN_MARGIN, LBPH_SINGLE_ENROLL_CAP)
            if real_count == 1
            else LBPH_UNKNOWN_MARGIN
        )

        # Reject __UNKNOWN__ synthetic class
        candidate = self.labels.get(best_label)
        if candidate == "__UNKNOWN__":
            return None, 0.0

        # Hard distance gate
        if best_dist >= effective_margin:
            return None, 0.0

        # Convert distance to confidence
        confidence = _lbph_confidence(best_dist, effective_margin)

        # Minimum confidence floor — drop borderline/noise matches
        if confidence < MIN_DISPLAY_CONFIDENCE:
            return None, 0.0

        return candidate, confidence

    # ── Confirmation Buffer Helper ─────────────────────────────────────────

    def _get_buffer(self, rect: tuple) -> _ConfirmBuffer:
        """Return (or create) the confirmation buffer for a face's grid cell."""
        x, y, _, _ = rect
        key = f"{x // 60}_{y // 60}"
        if key not in self._confirm_buffers:
            self._confirm_buffers[key] = _ConfirmBuffer()
        return self._confirm_buffers[key]

    # ── Drawing ────────────────────────────────────────────────────────────

    def _draw_result(
        self,
        frame: np.ndarray,
        rect: tuple,
        name,
        confidence: float
    ) -> None:
        """
        Draw the bounding box and recognition label on the frame.

        Known face (confidence >= threshold):
          - Green box + name + confidence %
        Unknown face:
          - Red box + "Unknown" label
        """
        x, y, w, h = rect
        conf_pct   = int(confidence * 100)

        if name is not None:
            # ── Known person ──────────────────────────────────────
            if conf_pct >= 70:
                color = (0, 200, 80)    # Green — high confidence
            elif conf_pct >= 55:
                color = (0, 180, 255)   # Orange — medium confidence
            else:
                color = (0, 140, 255)   # Amber — low but accepted

            # Bounding box
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)

            # Header bar above box
            header_top = max(0, y - 48)
            cv2.rectangle(frame, (x, header_top), (x + w, y), color, cv2.FILLED)

            # Name + confidence text
            label = f"{name}  {conf_pct}%"
            cv2.putText(
                frame, label,
                (x + 4, y - 28),
                cv2.FONT_HERSHEY_DUPLEX, 0.55,
                (255, 255, 255), 1, cv2.LINE_AA
            )

            # Confidence bar (thin strip below header text)
            bar_x2 = x + int(w * confidence)
            cv2.rectangle(frame, (x, y - 6), (bar_x2, y - 2), color, cv2.FILLED)

        else:
            # ── Unknown person ────────────────────────────────────
            color = (0, 50, 200)   # Red

            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)

            header_top = max(0, y - 30)
            cv2.rectangle(frame, (x, header_top), (x + w, y), color, cv2.FILLED)

            cv2.putText(
                frame, "Unknown",
                (x + 4, y - 8),
                cv2.FONT_HERSHEY_DUPLEX, 0.50,
                (255, 255, 255), 1, cv2.LINE_AA
            )

    def _draw_status_bar(self, frame: np.ndarray, face_count: int) -> None:
        """Draw a thin status bar at the bottom of the frame."""
        h, w = frame.shape[:2]
        cv2.rectangle(frame, (0, h - 32), (w, h), (15, 15, 15), cv2.FILLED)
        status = (
            f"Faces detected: {face_count}  |  "
            f"Model: LBPH  |  "
            f"Confirm: {CONFIRM_FRAMES_REQUIRED} frames  |  "
            f"[Q] Quit"
        )
        cv2.putText(
            frame, status,
            (6, h - 10),
            cv2.FONT_HERSHEY_SIMPLEX, 0.34,
            (140, 140, 140), 1, cv2.LINE_AA
        )

    # ── Main Loop ──────────────────────────────────────────────────────────

    def run(self) -> None:
        """
        Open the webcam and run real-time face recognition.
        Press Q to exit.
        """
        if self.model is None:
            print("[ABORT] No model loaded. Exiting.")
            return

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[ERROR] Cannot open webcam (device 0).")
            return

        print("\n[INFO] Camera started. Press Q to quit.\n")

        while True:
            ret, frame = cap.read()
            if not ret:
                print("[WARNING] Failed to read frame from camera.")
                break

            # ── Detect faces ──────────────────────────────────────────────
            faces = self._detect_faces(frame)

            for rect in faces:
                # ── Recognise ─────────────────────────────────────────────
                name, confidence = self._recognize_face(frame, rect)

                # ── Confirmation buffer — reduces flickering ───────────────
                buf = self._get_buffer(rect)
                buf.push(name, confidence)
                confirmed_name, confirmed_conf = buf.get()

                # Draw result (use confirmed identity if available, else raw)
                display_name = confirmed_name if confirmed_name else name
                display_conf = confirmed_conf if confirmed_name else confidence
                self._draw_result(frame, rect, display_name, display_conf)

            # ── Status bar ────────────────────────────────────────────────
            self._draw_status_bar(frame, len(faces))

            cv2.imshow("Smart Attendance System — LBPH", frame)

            # Press Q to quit
            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("[INFO] Q pressed — exiting.")
                break

        cap.release()
        cv2.destroyAllWindows()


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    recognizer = FaceRecognizer()
    recognizer.run()
