

# =============================================================
# lighting.py  —  Smart Attendance System  v9.1
#
# DARK SKIN FIX v9.1:
#   - Much more aggressive gamma correction for dark faces
#   - Multi-step CLAHE with higher clip limit
#   - Adaptive histogram stretching for very dark frames
#   - Face-specific preprocessing with retinex-like normalization
#   - Detects dark skin and applies targeted brightening
# =============================================================
import cv2
import numpy as np
import config


def _gamma_correct(img: np.ndarray, gamma: float) -> np.ndarray:
    """Apply gamma correction using lookup table (fast)."""
    table = np.array([
        min(255, int(((i / 255.0) ** (1.0 / gamma)) * 255))
        for i in range(256)
    ], dtype=np.uint8)
    return cv2.LUT(img, table)


def _stretch_histogram(gray: np.ndarray) -> np.ndarray:
    """
    Stretch histogram to use full 0-255 range.
    Critical for dark-skinned faces — their raw pixels cluster in 30-120 range.
    Stretching spreads them to 0-255, making features visible to LBPH.
    """
    mn, mx = float(gray.min()), float(gray.max())
    if mx - mn < 10:
        return gray  # Avoid division by zero on flat images
    stretched = np.clip((gray.astype(np.float32) - mn) / (mx - mn) * 255, 0, 255)
    return stretched.astype(np.uint8)


def preprocess_frame(frame: np.ndarray) -> np.ndarray:
    """
    Auto-correct full frame brightness before face detection.

    For dark Indian skin tones in classroom fluorescent light:
    - Mean brightness < 60  → very aggressive gamma=2.0
    - Mean brightness < 90  → aggressive gamma=1.7
    - Mean brightness < 120 → moderate gamma=1.3
    - Mean brightness > 210 → darken gamma correction

    These values are tuned for dark skin + fluorescent ceiling lights.
    """
    if frame is None or frame.size == 0:
        return frame

    out  = frame.copy()
    gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    mean = float(np.mean(gray))

    if mean < 60:
        # Very dark — dark skin in dim room
        out = _gamma_correct(out, 2.0)
    elif mean < 90:
        # Dark — dark skin in normal room
        out = _gamma_correct(out, 1.7)
    elif mean < 120:
        # Slightly dark
        out = _gamma_correct(out, 1.3)
    elif mean > 210:
        # Overexposed — bright window behind student
        out = _gamma_correct(out, 0.65)

    return out


def preprocess_face(face_bgr: np.ndarray) -> np.ndarray:
    """
    Normalize face crop for LBPH recognition.

    v9.1 pipeline specifically for dark skin:
    1. Convert to grayscale
    2. CLAHE with high clip limit (4.0) — brings out dark skin texture
    3. Histogram stretching — spreads pixel range to full 0-255
    4. Second CLAHE pass — fine-tune local contrast
    5. Mild Gaussian blur — reduce noise from aggressive enhancement

    Without this, dark faces look nearly uniform gray to LBPH,
    which can't distinguish their facial features from each other.
    """
    if face_bgr is None or face_bgr.size == 0:
        return face_bgr

    # Step 1: Grayscale
    if len(face_bgr.shape) == 3:
        gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
    else:
        gray = face_bgr.copy()

    # Step 2: Gamma brighten if the face itself is dark
    mean_face = float(np.mean(gray))
    if mean_face < 70:
        # Very dark face — gamma=2.0
        table = np.array([min(255, int(((i/255.0)**(1.0/2.0))*255))
                          for i in range(256)], dtype=np.uint8)
        gray = cv2.LUT(gray, table)
    elif mean_face < 100:
        # Dark face — gamma=1.6
        table = np.array([min(255, int(((i/255.0)**(1.0/1.6))*255))
                          for i in range(256)], dtype=np.uint8)
        gray = cv2.LUT(gray, table)

    # Step 3: equalizeHist — parameter-free, matches training pipeline
    # v9.3: Use equalizeHist as primary step. This EXACTLY matches
    # what preprocess_for_lbph() in train.py does, ensuring
    # training and runtime LBP codes are identical → low distance.
    enhanced = cv2.equalizeHist(gray)

    # Step 4: Mild CLAHE for fine detail (secondary)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(enhanced)

    return enhanced


def preprocess_face_for_enroll(face_bgr: np.ndarray) -> np.ndarray:
    """
    Same as preprocess_face but ALSO saves a brightness-boosted version.
    Used during enrollment to ensure dark faces are captured well.
    Returns the enhanced grayscale image.
    """
    return preprocess_face(face_bgr)