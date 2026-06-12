
# =============================================================
# twin_analysis.py  —  Smart Attendance System  v8.3
#
# FIXES v8.3:
#  - db_module.get_student() now exists in database.py
#  - log_twin_analysis() call uses correct keyword args
#  - All imports verified against updated database.py
# =============================================================
import cv2
import numpy as np
import os
import pickle
import logging
from collections import deque, Counter

import config
import database as db_module

log = logging.getLogger(__name__)

# ── Use shared MediaPipe instance from liveness ────────────────
_mp_pose = None
MP_OK    = False
try:
    from liveness import get_shared_pose, MP_OK as _MP_OK
    _mp_pose = get_shared_pose()
    MP_OK    = _MP_OK
except Exception as e:
    log.warning("Could not get shared MediaPipe pose: %s", e)
    try:
        import mediapipe as mp
        _mp_pose = mp.solutions.pose.Pose(
            static_image_mode=False, model_complexity=2,
            smooth_landmarks=True,
            min_detection_confidence=0.6, min_tracking_confidence=0.6)
        MP_OK = True
    except Exception:
        pass

# dlib 68-point landmarks
DLIB_OK      = False
_dlib_det    = None
_predictor68 = None
try:
    import dlib
    _dlib_det  = dlib.get_frontal_face_detector()
    pred_path  = os.path.join(config.MODEL_DIR,
                               "shape_predictor_68_face_landmarks.dat")
    if os.path.exists(pred_path):
        _predictor68 = dlib.shape_predictor(pred_path)
        DLIB_OK      = True
        log.info("dlib 68-point predictor loaded.")
except Exception:
    pass

# HOG descriptor singleton
_HOG = cv2.HOGDescriptor(
    _winSize   = (80, 40),
    _blockSize = (20, 20),
    _blockStride = (10, 10),
    _cellSize  = (10, 10),
    _nbins     = 9,
)

# ==============================================================
# Feature Extractor 1: IRIS GABOR (32-d)
# ==============================================================
def extract_iris_gabor(face_bgr: np.ndarray) -> np.ndarray:
    if face_bgr is None or face_bgr.size == 0:
        return np.zeros(32, dtype=np.float32)
    gray = (cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
            if len(face_bgr.shape) == 3 else face_bgr.copy())
    gray = cv2.resize(gray, (200, 200))
    h, w = gray.shape
    feats = []
    eye_regions = [
        gray[int(h*0.22):int(h*0.48), int(w*0.03):int(w*0.45)],
        gray[int(h*0.22):int(h*0.48), int(w*0.55):int(w*0.97)],
    ]
    for eye in eye_regions:
        if eye.size == 0:
            feats.extend([0.0] * 16)
            continue
        eye_norm = cv2.resize(eye, (80, 40))
        blurred  = cv2.GaussianBlur(eye_norm, (5, 5), 0)
        _, iris  = cv2.threshold(blurred, 50, 255, cv2.THRESH_BINARY_INV)
        iris_f   = iris.astype(np.float32)
        for theta in np.linspace(0, np.pi, 8, endpoint=False):
            kernel = cv2.getGaborKernel((15, 15), 4.0, theta, 8.0, 0.5, 0,
                                        ktype=cv2.CV_32F)
            resp = cv2.filter2D(iris_f, cv2.CV_32F, kernel)
            feats.append(float(np.mean(np.abs(resp))))
            feats.append(float(np.std(resp)))
    return np.array(feats, dtype=np.float32)

# ==============================================================
# Feature Extractor 2: PERIOCULAR HOG (36-d)
# ==============================================================
def extract_periocular_hog(face_bgr: np.ndarray) -> np.ndarray:
    if face_bgr is None or face_bgr.size == 0:
        return np.zeros(36, dtype=np.float32)
    gray = (cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
            if len(face_bgr.shape) == 3 else face_bgr.copy())
    gray = cv2.resize(gray, (200, 200))
    h, w = gray.shape
    feats = []
    periocular_regions = [
        gray[int(h*0.15):int(h*0.55), int(w*0.02):int(w*0.48)],
        gray[int(h*0.15):int(h*0.55), int(w*0.52):int(w*0.98)],
    ]
    for region in periocular_regions:
        if region.size == 0:
            feats.extend([0.0] * 18)
            continue
        r      = cv2.resize(region, (80, 40))
        r      = cv2.equalizeHist(r)
        h_desc = _HOG.compute(r)
        if h_desc is not None and len(h_desc) > 0:
            arr = h_desc.flatten()[:18]
            if len(arr) < 18:
                arr = np.pad(arr, (0, 18 - len(arr)))
            feats.extend(arr.tolist())
        else:
            feats.extend([0.0] * 18)
    return np.array(feats, dtype=np.float32)

# ==============================================================
# Feature Extractor 3: SKELETON GEOMETRY (12-d)
# ==============================================================
def extract_skeleton_geometry(frame_bgr: np.ndarray) -> np.ndarray:
    if not MP_OK or _mp_pose is None or frame_bgr is None:
        return np.zeros(12, dtype=np.float32)
    try:
        result = _mp_pose.process(frame_bgr[:, :, ::-1])
        if not result.pose_landmarks:
            return np.zeros(12, dtype=np.float32)
        lm = result.pose_landmarks.landmark
        def pt(i):
            return np.array([lm[i].x, lm[i].y], dtype=np.float32)
        def dist(a, b):
            return float(np.linalg.norm(pt(a) - pt(b)) + 1e-6)

        shoulder_w = dist(11, 12)
        hip_w      = dist(23, 24)
        torso_h    = dist(11, 23)
        head_torso = dist(0, 11)
        l_arm      = dist(11, 13) + dist(13, 15)
        r_arm      = dist(12, 14) + dist(14, 16)
        l_leg      = dist(23, 25) + dist(25, 27)
        r_leg      = dist(24, 26) + dist(26, 28)

        feats = [
            shoulder_w / (hip_w + 1e-6),
            hip_w / (shoulder_w + 1e-6),
            torso_h / (shoulder_w + 1e-6),
            head_torso / (torso_h + 1e-6),
            l_arm / (torso_h + 1e-6),
            r_arm / (torso_h + 1e-6),
            l_leg / (torso_h + 1e-6),
            r_leg / (torso_h + 1e-6),
            l_arm / (r_arm + 1e-6),
            l_leg / (r_leg + 1e-6),
            shoulder_w / (torso_h + 1e-6),
            hip_w / (torso_h + 1e-6),
        ]
        return np.array(feats, dtype=np.float32)
    except Exception:
        return np.zeros(12, dtype=np.float32)

# ==============================================================
# Feature Extractor 4: FACIAL GEOMETRY (18-d)
# ==============================================================
def extract_facial_geometry(face_bgr: np.ndarray) -> np.ndarray:
    if not DLIB_OK or face_bgr is None or face_bgr.size == 0:
        return np.zeros(18, dtype=np.float32)
    try:
        gray = (cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
                if len(face_bgr.shape) == 3 else face_bgr)
        gray = cv2.resize(gray, (200, 200))
        dets = _dlib_det(gray, 0)
        if not dets:
            return np.zeros(18, dtype=np.float32)
        shape = _predictor68(gray, dets[0])
        pts   = np.array([[shape.part(i).x, shape.part(i).y]
                           for i in range(68)], dtype=np.float32)

        def d(a, b):
            return float(np.linalg.norm(pts[a] - pts[b]) + 1e-6)

        iod     = d(36, 45)
        nose_w  = d(31, 35)
        mouth_w = d(48, 54)
        jaw_w   = d(0, 16)
        left_ey = d(36, 39)
        right_ey= d(42, 45)
        feats = [
            iod / (jaw_w + 1e-6),
            nose_w / (iod + 1e-6),
            mouth_w / (jaw_w + 1e-6),
            left_ey / (right_ey + 1e-6),
            d(19, 24) / (iod + 1e-6),
            d(27, 30) / (jaw_w + 1e-6),
            d(33, 51) / (jaw_w + 1e-6),
            d(17, 26) / (jaw_w + 1e-6),
            d(0, 8)   / (jaw_w + 1e-6),
            d(16, 8)  / (jaw_w + 1e-6),
            d(36, 31) / (iod + 1e-6),
            d(45, 35) / (iod + 1e-6),
            d(48, 57) / (mouth_w + 1e-6),
            d(60, 64) / (mouth_w + 1e-6),
            d(17, 19) / (iod + 1e-6),
            d(24, 26) / (iod + 1e-6),
            nose_w / (jaw_w + 1e-6),
            mouth_w / (iod + 1e-6),
        ]
        return np.array(feats, dtype=np.float32)
    except Exception:
        return np.zeros(18, dtype=np.float32)

# ==============================================================
# Feature Extractor 5: SKIN LBP (26-d)
# ==============================================================
def extract_skin_lbp(face_bgr: np.ndarray) -> np.ndarray:
    if face_bgr is None or face_bgr.size == 0:
        return np.zeros(26, dtype=np.float32)
    try:
        gray  = (cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
                 if len(face_bgr.shape) == 3 else face_bgr)
        gray  = cv2.resize(gray, (200, 200))
        h, w  = gray.shape
        cheek = gray[int(h*0.45):int(h*0.75), int(w*0.15):int(w*0.85)]
        if cheek.size == 0:
            return np.zeros(26, dtype=np.float32)
        g     = cheek.astype(np.float32)
        lbp   = np.zeros_like(g, dtype=np.uint8)
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dy == 0 and dx == 0:
                    continue
                lbp += (np.roll(np.roll(g, dy, 0), dx, 1) > g).astype(np.uint8)
        hist, _ = np.histogram(lbp.ravel(), bins=26, range=(0, 9))
        hist    = hist.astype(np.float32) / (hist.sum() + 1e-6)
        return hist
    except Exception:
        return np.zeros(26, dtype=np.float32)

# ==============================================================
# Combined 124-d feature vector
# ==============================================================
def extract_twin_features(face_bgr: np.ndarray,
                          frame_bgr: np.ndarray) -> dict:
    iris_fv  = extract_iris_gabor(face_bgr)
    peri_fv  = extract_periocular_hog(face_bgr)
    skel_fv  = extract_skeleton_geometry(frame_bgr)
    geom_fv  = extract_facial_geometry(face_bgr)
    lbp_fv   = extract_skin_lbp(face_bgr)
    full     = np.concatenate([iris_fv, peri_fv, skel_fv, geom_fv, lbp_fv])
    return {
        "full":  full,
        "iris":  iris_fv,
        "peri":  peri_fv,
        "skel":  skel_fv,
        "geom":  geom_fv,
        "lbp":   lbp_fv,
    }

# ==============================================================
# TRAINING
# ==============================================================
def train_twin_model() -> bool:
    print("\n─── Twin SVM Training v8.3 ───")
    try:
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.decomposition import PCA
        from sklearn.svm import SVC
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.model_selection import StratifiedKFold, cross_val_score, cross_val_predict
        from sklearn.metrics import classification_report
    except ImportError:
        print("  scikit-learn not installed.")
        return False

    pairs = db_module.get_all_twin_pairs()
    if not pairs:
        print("  No twin pairs registered.")
        return False

    all_models = {}
    for pair in pairs:
        id1, id2 = pair["id1"], pair["id2"]
        n1,  n2  = pair["name1"], pair["name2"]
        print(f"\n  Training: {n1} vs {n2}")

        X, y = [], []
        for label, sid in enumerate([id1, id2]):
            src = os.path.join(config.KNOWN_FACES_DIR, sid)
            if not os.path.isdir(src):
                print(f"    WARNING: {src} not found — skipping")
                continue
            imgs = [f for f in os.listdir(src)
                    if f.lower().endswith((".jpg", ".jpeg", ".png"))]

            for fname in imgs:
                img = cv2.imread(os.path.join(src, fname))
                if img is None:
                    continue
                img = cv2.resize(img, (160, 160))
                fv_dict = extract_twin_features(img, img)
                X.append(fv_dict["full"])
                y.append(label)

        if len(X) < 10 or len(set(y)) < 2:
            print(f"    Insufficient data ({len(X)} samples)")
            continue

        X_arr = np.array(X, dtype=np.float32)
        y_arr = np.array(y, dtype=np.int32)

        pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("pca",    PCA(n_components=0.95)),
            ("svm",    CalibratedClassifierCV(
                SVC(kernel="rbf", C=10, gamma="scale",
                    class_weight="balanced"),
                cv=min(5, len(y_arr) // 2))),
        ])

        if len(y_arr) >= 10:
            cv = StratifiedKFold(n_splits=min(5, len(y_arr) // 2),
                                  shuffle=True, random_state=42)
            try:
                scores   = cross_val_score(pipeline, X_arr, y_arr,
                                            cv=cv, scoring="accuracy")
                y_pred   = cross_val_predict(pipeline, X_arr, y_arr, cv=cv)
                print(f"    CV Accuracy: {scores.mean()*100:.1f}% "
                      f"+/-{scores.std()*100:.1f}%")
                print(classification_report(
                    y_arr, y_pred, target_names=[n1, n2]))
            except Exception as e:
                print(f"    CV warning: {e}")

        pipeline.fit(X_arr, y_arr)
        all_models[f"{id1}_{id2}"] = {
            "model":     pipeline,
            "id1":       id1,
            "id2":       id2,
            "name1":     n1,
            "name2":     n2,
            "label_map": {0: id1, 1: id2},
            "n_samples": len(X),
        }
        print(f"  Twin model trained: {n1} vs {n2} ({len(X)} samples)")

    if not all_models:
        print("No twin models trained.")
        return False

    os.makedirs(config.MODEL_DIR, exist_ok=True)
    with open(config.TWIN_MODEL, "wb") as f:
        pickle.dump(all_models, f)
    print(f"\nTwin models saved → {len(all_models)} pair(s)")
    return True


# ==============================================================
# TWIN PREDICTOR — Runtime
# ==============================================================
class TwinPredictor:
    def __init__(self):
        self.models   = {}
        self._history = {}
        self._load()

    def _load(self):
        if not os.path.exists(config.TWIN_MODEL):
            log.warning("Twin model not found — train with [2] in main menu.")
            return
        try:
            with open(config.TWIN_MODEL, "rb") as f:
                self.models = pickle.load(f)
            log.info("Twin models loaded: %s", list(self.models.keys()))
        except Exception as e:
            log.error("Twin model load error: %s", e)

    def _get_model(self, id1: str, id2: str):
        for key in [f"{id1}_{id2}", f"{id2}_{id1}"]:
            if key in self.models:
                return self.models[key]
        return None

    def predict(self, face_bgr: np.ndarray, frame_bgr: np.ndarray,
                candidate_id: str, twin_partner_id: str,
                period: str = "") -> dict:
        result = {
            "student_id":       candidate_id,
            "confidence":       0.5,
            "twin_verified":    False,
            "iris_score":       0.0,
            "skeleton_score":   0.0,
            "periocular_score": 0.0,
            "geometry_score":   0.0,
            "decision":         "uncertain",
        }

        model_data = self._get_model(candidate_id, twin_partner_id)
        if model_data is None:
            return result

        try:
            pipeline  = model_data["model"]
            label_map = model_data["label_map"]
            id1       = model_data["id1"]

            fv_dict = extract_twin_features(face_bgr, frame_bgr)
            fv_full = fv_dict["full"]

            prob = pipeline.predict_proba(fv_full.reshape(1, -1))[0]
            label_for_cand = 0 if candidate_id == id1 else 1
            label_for_twin = 1 - label_for_cand

            conf_cand = float(prob[label_for_cand])
            conf_twin = float(prob[label_for_twin])

            if conf_cand >= conf_twin:
                winner, conf_win = candidate_id, conf_cand
            else:
                winner, conf_win = twin_partner_id, conf_twin

            margin       = abs(conf_cand - conf_twin)
            iris_sc      = float(np.clip(margin * (1.0 + np.mean(np.abs(fv_dict["iris"])) * 0.1), 0, 1))
            peri_sc      = float(np.clip(margin * (1.0 + np.mean(np.abs(fv_dict["peri"])) * 0.1), 0, 1))
            geom_sc      = float(np.clip(margin * (1.0 + np.mean(np.abs(fv_dict["geom"])) * 0.1), 0, 1))
            skel_raw     = float(np.mean(np.abs(fv_dict["skel"])))
            skel_sc      = float(np.clip(skel_raw / 2.0, 0.0, 1.0))

            # Temporal smoothing
            key = f"{candidate_id}_{twin_partner_id}"
            if key not in self._history:
                self._history[key] = deque(maxlen=6)
            self._history[key].append((winner, conf_win))

            if len(self._history[key]) >= 3:
                votes    = [h[0] for h in self._history[key]]
                winner   = Counter(votes).most_common(1)[0][0]
                conf_win = float(np.mean([c for w, c in self._history[key]
                                          if w == winner]))

            decision = "twin_A" if winner == id1 else "twin_B"
            accepted = conf_win >= config.TWIN_MIN_CONFIDENCE

            # Log to database — using correct v8.3 signature
            try:
                db_module.log_twin_analysis(
                    student_id       = winner,
                    name             = "",
                    twin_id          = (twin_partner_id
                                        if winner == candidate_id
                                        else candidate_id),
                    verified         = accepted,
                    confidence       = conf_win,
                    method           = "svm",
                    period           = period,
                    iris_score       = iris_sc,
                    skeleton_score   = skel_sc,
                    periocular_score = peri_sc,
                    geometry_score   = geom_sc,
                    final_confidence = conf_win,
                    decision         = decision,
                    feature_vector   = str(fv_full[:10].tolist()),
                )
            except Exception as db_err:
                log.warning("twin log DB error (non-fatal): %s", db_err)

            result.update({
                "student_id":       winner if accepted else candidate_id,
                "confidence":       conf_win,
                "twin_verified":    accepted,
                "iris_score":       round(iris_sc,  4),
                "skeleton_score":   round(skel_sc,  4),
                "periocular_score": round(peri_sc,  4),
                "geometry_score":   round(geom_sc,  4),
                "decision":         decision,
            })

            if accepted:
                s    = db_module.get_student(winner)
                name = s["name"] if s else winner
                log.info("TWIN ID: %s | conf=%.1f%% | skeleton=%.3f | iris=%.3f",
                         name, conf_win*100, skel_sc, iris_sc)
            else:
                log.info("TWIN uncertain (%.1f%% < %.0f%%) — defaulting",
                         conf_win*100, config.TWIN_MIN_CONFIDENCE*100)

        except Exception as e:
            log.error("Twin predict error: %s", e, exc_info=True)

        return result