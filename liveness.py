
# =============================================================
# liveness.py  —  Smart Attendance System  v8.1
#
# KEY CHANGES:
#  - LIVENESS_THRESHOLD in config lowered to 0.30 (was 0.38)
#  - Passive liveness more tolerant for real classroom conditions
#  - 8th signal: color channel variance + Laplacian (anti-spoofing)
#  - Blink detection fallback when shape predictor missing
#  - Shared MediaPipe singleton for performance
# =============================================================
import cv2
import numpy as np
import logging
import config

log = logging.getLogger(__name__)

# ── MediaPipe Pose singleton ──────────────────────────────────
_shared_pose = None
MP_OK        = False

_MP_INIT_DONE = False  # Only try once — prevents spam warnings

def get_shared_pose():
    global _shared_pose, MP_OK, _MP_INIT_DONE
    if _MP_INIT_DONE:
        return _shared_pose  # Return cached (None if failed)
    _MP_INIT_DONE = True
    try:
        import mediapipe as mp
        _shared_pose = mp.solutions.pose.Pose(
            static_image_mode       = False,
            model_complexity        = 1,
            smooth_landmarks        = True,
            min_detection_confidence= 0.55,
            min_tracking_confidence = 0.55,
        )
        MP_OK = True
        log.info("MediaPipe Pose active — skeleton liveness enabled.")
    except Exception as e:
        log.warning("MediaPipe not available: %s — skeleton disabled.", e)
        _shared_pose = None
        MP_OK = False
    return _shared_pose


# Call once at import to create the singleton
get_shared_pose()


def _eye_aspect_ratio(eye_pts: np.ndarray) -> float:
    """EAR = blink metric. Range 0 (closed) to ~0.5 (open)."""
    v1 = np.linalg.norm(eye_pts[1] - eye_pts[5])
    v2 = np.linalg.norm(eye_pts[2] - eye_pts[4])
    h  = np.linalg.norm(eye_pts[0] - eye_pts[3])
    return float((v1 + v2) / (2.0 * h + 1e-6))


def skeleton_live_score(frame_bgr: np.ndarray) -> float:
    """
    Estimate liveness from pose landmarks.
    Returns 0.0–1.0.  0.65 returned when MediaPipe unavailable.
    """
    pose = get_shared_pose()
    if not MP_OK or pose is None or frame_bgr is None:
        return 0.65
    try:
        rgb    = frame_bgr[:, :, ::-1]
        result = pose.process(rgb)
        if not result.pose_landmarks:
            return 0.50
        lms = result.pose_landmarks.landmark
        vis_scores = [lms[i].visibility for i in [0, 11, 12, 23, 24]]
        avg_vis    = float(np.mean(vis_scores))
        return float(np.clip(avg_vis, 0.0, 1.0))
    except Exception:
        return 0.65


class LivenessDetector:
    """
    Combines multiple passive liveness signals into a single score.
    Signals:
      1. Eye Aspect Ratio (blink cadence)
      2. Texture variance (blur = print attack)
      3. Frequency domain energy
      4. Laplacian sharpness
      5. Color channel variance (mono screen = low variance)
      6. Specular highlight (shiny print = high glare)
      7. Skeleton visibility via MediaPipe
      8. Passive color/edge combination signal
    """

    def __init__(self):
        self._blink_count    = 0
        self._ear_below      = False
        self._scores         = []       # rolling window of liveness scores
        self._frame_count    = 0
        self._last_score     = 0.65
        self._skel_score     = 0.65

    def update(self, frame: np.ndarray, face_rect: tuple,
               landmarks=None, skel_ext: float = None) -> dict:
        self._frame_count += 1
        x, y, w, h = face_rect
        fh, fw     = frame.shape[:2]

        face = frame[max(0,y):min(fh,y+h), max(0,x):min(fw,x+w)]
        if face is None or face.size == 0:
            return self._result(0.50)

        gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)

        signals = []

        # ── Signal 1: Blink / EAR ────────────────────────────
        ear_score = 0.65  # neutral when no predictor
        if landmarks is not None:
            try:
                l_eye = np.array([[landmarks.part(i).x,
                                   landmarks.part(i).y]
                                   for i in range(36, 42)], dtype=np.float32)
                r_eye = np.array([[landmarks.part(i).x,
                                   landmarks.part(i).y]
                                   for i in range(42, 48)], dtype=np.float32)
                ear = (_eye_aspect_ratio(l_eye) +
                       _eye_aspect_ratio(r_eye)) / 2.0

                if ear < config.EYE_AR_THRESHOLD:
                    if not self._ear_below:
                        self._ear_below = True
                else:
                    if self._ear_below:
                        self._blink_count += 1
                        self._ear_below = False

                # More blinks = more likely real
                ear_score = min(1.0, 0.50 + self._blink_count * 0.10)
            except Exception:
                ear_score = 0.65
        signals.append(ear_score)

        # ── Signal 2: Texture variance (real face = high) ────
        blur_val   = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        tex_score  = float(np.clip(blur_val / 500.0, 0.0, 1.0))
        signals.append(tex_score)

        # ── Signal 3: Frequency energy (real face = rich) ────
        fft        = np.fft.fft2(gray.astype(np.float32))
        fft_shift  = np.fft.fftshift(fft)
        magnitude  = np.log1p(np.abs(fft_shift))
        freq_score = float(np.clip(np.std(magnitude) / 3.0, 0.0, 1.0))
        signals.append(freq_score)

        # ── Signal 4: Laplacian sharpness ────────────────────
        lap_var    = float(cv2.Laplacian(
            cv2.resize(gray, (64, 64)), cv2.CV_64F).var())
        sharp_score= float(np.clip(lap_var / 300.0, 0.0, 1.0))
        signals.append(sharp_score)

        # ── Signal 5: Color channel variance (anti-screen) ───
        if len(face.shape) == 3 and face.shape[2] == 3:
            b_mean = float(np.mean(face[:,:,0]))
            g_mean = float(np.mean(face[:,:,1]))
            r_mean = float(np.mean(face[:,:,2]))
            # Real skin: channels differ. Screen: similar gray channels.
            channel_diff = np.std([b_mean, g_mean, r_mean])
            color_score  = float(np.clip(channel_diff / 25.0, 0.0, 1.0))
        else:
            color_score = 0.65
        signals.append(color_score)

        # ── Signal 6: Specular highlight (print attack) ──────
        # A real face has limited bright spots; a photo has many
        bright_px    = float(np.mean(gray > 240))
        specular_sc  = float(np.clip(1.0 - bright_px * 8.0, 0.0, 1.0))
        signals.append(specular_sc)

        # ── Signal 7: Skeleton visibility ────────────────────
        if skel_ext is not None:
            self._skel_score = float(skel_ext)
        else:
            if self._frame_count % 8 == 0:
                self._skel_score = skeleton_live_score(frame)
        signals.append(self._skel_score)

        # ── Signal 8: Passive edge + color (anti-spoofing) ───
        edges      = cv2.Canny(gray, 30, 100)
        edge_dens  = float(np.mean(edges > 0))
        passive_sc = float(np.clip(edge_dens * 3.0 + color_score * 0.5, 0.0, 1.0))
        signals.append(passive_sc)

        # ── Weighted combination ──────────────────────────────
        # Weights: blink=0.10, texture=0.15, freq=0.10, sharp=0.15,
        #          color=0.15, specular=0.10, skeleton=0.15, passive=0.10
        weights = [0.10, 0.15, 0.10, 0.15, 0.15, 0.10, 0.15, 0.10]
        score   = float(np.dot(signals[:len(weights)], weights[:len(signals)]))
        score   = float(np.clip(score, 0.0, 1.0))

        # Rolling smoothing
        self._scores.append(score)
        if len(self._scores) > 10:
            self._scores.pop(0)
        smooth = float(np.mean(self._scores))

        self._last_score = smooth
        is_live = smooth >= config.LIVENESS_THRESHOLD  # now 0.30

        return {
            "live":          is_live,
            "score":         smooth,
            "skeleton_score":self._skel_score,
            "signals":       signals,
            "blink_count":   self._blink_count,
        }

    def _result(self, score: float) -> dict:
        return {
            "live":          score >= config.LIVENESS_THRESHOLD,
            "score":         score,
            "skeleton_score":self._skel_score,
            "signals":       [],
            "blink_count":   self._blink_count,
        }