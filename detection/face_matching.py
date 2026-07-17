import os
import cv2
import numpy as np
from scipy.spatial.distance import cosine

try:
    import dlib
except ImportError:
    dlib = None

try:
    import face_recognition
except ImportError:
    face_recognition = None

# -----------------------------
# Models
# -----------------------------
datFile = os.path.join(os.path.dirname(__file__), "shape_predictor_68_face_landmarks.dat")

# Haar cascade (fallback)
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

# dlib HOG detector (primary when available)
detector = dlib.get_frontal_face_detector() if dlib is not None else None
predictor = None
if dlib is not None and os.path.isfile(datFile):
    predictor = dlib.shape_predictor(datFile)


# -----------------------------
# Face detection (dlib first)
# CHANGED: downscale before detection + upsample=0 for speed (Fix 2)
# -----------------------------
DETECT_MAX_DIM = 480  # CHANGED: cap longest side before running dlib HOG


def detect_faces(img):
    """
    Returns list of (x, y, w, h) in ORIGINAL image coordinates.

    CHANGED (performance):
    - Downscales the frame before running dlib's HOG detector, since dlib's
      cost scales roughly with pixel count and upsample passes.
    - Uses upsample=0 instead of upsample=1 (upsample=1 doubles internal
      resolution and roughly doubles detection time) since faces captured on
      a webcam at typical distance are still easily detected at upsample=0
      once we've already downscaled sensibly.
    - Detected boxes are scaled back up to original image coordinates so
      nothing downstream (landmarks, cropping, embeddings) needs to change.
    """
    if img is None:
        return []

    h, w = img.shape[:2]
    scale = 1.0
    if max(h, w) > DETECT_MAX_DIM:
        scale = DETECT_MAX_DIM / float(max(h, w))
        small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    else:
        small = img

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    # 1) dlib HOG detector
    if detector is not None:
        rects = detector(gray, 0)  # CHANGED: upsample=0 instead of 1
        faces = []
        for r in rects:
            x = max(0, int(r.left() / scale))
            y = max(0, int(r.top() / scale))
            fw = max(0, int((r.right() - r.left()) / scale))
            fh = max(0, int((r.bottom() - r.top()) / scale))
            if fw > 0 and fh > 0:
                faces.append((x, y, fw, fh))

        if len(faces) > 0:
            return faces

    # 2) Haar fallback (also run on the downscaled gray frame, then rescale)
    faces = face_cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=6, minSize=(40, 40)
    )
    if faces is None:
        return []
    return [
        (int(x / scale), int(y / scale), int(w_ / scale), int(h_ / scale))
        for (x, y, w_, h_) in faces
    ]


# -----------------------------
# Alignment
# -----------------------------
def align_face(img, face):
    """
    Align using eye centres (dlib landmarks).
    face = (x,y,w,h)
    """
    if img is None or face is None:
        return img

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    x, y, w, h = face

    if dlib is None or predictor is None:
        return img

    rect = dlib.rectangle(int(x), int(y), int(x + w), int(y + h))

    try:
        shape = predictor(gray, rect)
    except Exception:
        return img

    shape = np.array(
        [(shape.part(j).x, shape.part(j).y) for j in range(shape.num_parts)],
        dtype=np.float32
    )

    desired_face_width = 256
    desired_face_height = desired_face_width

    left_eye_landmarks = [36, 37, 38, 39, 40, 41]
    right_eye_landmarks = [42, 43, 44, 45, 46, 47]

    left_eye_center = np.mean(shape[left_eye_landmarks], axis=0).astype(np.float32)
    right_eye_center = np.mean(shape[right_eye_landmarks], axis=0).astype(np.float32)

    dY = float(right_eye_center[1] - left_eye_center[1])
    dX = float(right_eye_center[0] - left_eye_center[0])
    angle = float(np.degrees(np.arctan2(dY, dX)))

    dist = float(np.sqrt((dX ** 2) + (dY ** 2)))
    if dist <= 1e-6:
        return img

    desired_dist = desired_face_width * 0.27
    scale = desired_dist / dist

    eyes_center = (
        float((left_eye_center[0] + right_eye_center[0]) / 2.0),
        float((left_eye_center[1] + right_eye_center[1]) / 2.0),
    )

    M = cv2.getRotationMatrix2D((eyes_center[0], eyes_center[1]), angle, scale)

    tX = desired_face_width * 0.5
    tY = desired_face_height * 0.3
    M[0, 2] += (tX - eyes_center[0])
    M[1, 2] += (tY - eyes_center[1])

    output = cv2.warpAffine(
        img, M, (desired_face_width, desired_face_height),
        flags=cv2.INTER_CUBIC
    )
    return output


# -----------------------------
# Embeddings
# -----------------------------
def extract_features(face):
    """
    Returns 128D face embedding (np.ndarray shape (128,))
    """
    if face is None:
        return None

    if face_recognition is None:
        gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, (16, 8), interpolation=cv2.INTER_AREA)
        emb = resized.astype(np.float32).reshape(-1) / 255.0
        norm = np.linalg.norm(emb)
        return emb / norm if norm > 1e-6 else emb

    rgb = cv2.cvtColor(face, cv2.COLOR_BGR2RGB)
    encodings = face_recognition.face_encodings(rgb)

    if len(encodings) == 0:
        return None

    return encodings[0]


# -----------------------------
# Matching (stricter)
# -----------------------------
def match_face(embedding, database):
    """
    database: dict {name: embedding_list}
    Returns name or None.
    """
    if embedding is None:
        return None

    emb_q = np.asarray(embedding, dtype=np.float32).reshape(-1)
    if emb_q.size != 128 or np.any(np.isnan(emb_q)) or np.any(np.isinf(emb_q)):
        return None

    names = []
    encs = []

    for name, db_embedding in database.items():
        if db_embedding is None:
            continue
        emb_d = np.asarray(db_embedding, dtype=np.float32).reshape(-1)
        if emb_d.size != 128 or np.any(np.isnan(emb_d)) or np.any(np.isinf(emb_d)):
            continue
        names.append(name)
        encs.append(emb_d)

    if not encs:
        return None

    encs = np.vstack(encs)  # (N, 128)

    # Euclidean is the usual face_recognition metric
    if face_recognition is not None:
        dists = face_recognition.face_distance(encs, emb_q)
    else:
        dists = np.linalg.norm(encs - emb_q, axis=1)
    best_idx = int(np.argmin(dists))
    best_euclid = float(dists[best_idx])

    # Extra safety: cosine distance must also be small
    best_cos = float(cosine(emb_q, encs[best_idx]))

    # 🔥 stricter thresholds to reduce false accepts
    EUCLID_TOL = 0.45
    COS_TOL = 0.30

    # Convert to confidence
    euclid_conf = float(np.exp(-best_euclid * 5))  # smoother decay
    cos_conf = float(1.0 - best_cos)

    final_conf = float((euclid_conf * 0.6) + (cos_conf * 0.4))

    match = best_euclid <= EUCLID_TOL and best_cos <= COS_TOL

    if match:
        return names[best_idx], best_euclid, final_conf

    return None, best_euclid, final_conf


# -----------------------------
# LIVENESS helpers (Blink + Head turn)
# -----------------------------
LEFT_EYE_IDX = [36, 37, 38, 39, 40, 41]
RIGHT_EYE_IDX = [42, 43, 44, 45, 46, 47]
NOSE_TIP_IDX = 33


def _eye_aspect_ratio(eye_pts):
    A = np.linalg.norm(eye_pts[1] - eye_pts[5])
    B = np.linalg.norm(eye_pts[2] - eye_pts[4])
    C = np.linalg.norm(eye_pts[0] - eye_pts[3])
    if C <= 1e-6:
        return 0.0
    return float((A + B) / (2.0 * C))


def get_landmarks(img, face_box):
    if img is None or face_box is None:
        return None

    x, y, w, h = face_box
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    if dlib is not None and predictor is not None:
        rect = dlib.rectangle(int(x), int(y), int(x + w), int(y + h))
        try:
            shape = predictor(gray, rect)
            pts = np.array(
                [(shape.part(i).x, shape.part(i).y) for i in range(shape.num_parts)],
                dtype=np.float32
            )
            if pts.shape[0] >= 68:
                return pts
        except Exception:
            pass

    if face_recognition is None:
        return None

    try:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        landmarks = face_recognition.face_landmarks(rgb, [(y, x + w, y + h, x)])
    except Exception:
        return None

    if not landmarks:
        return None

    lm = landmarks[0]
    left_eye = lm.get("left_eye")
    right_eye = lm.get("right_eye")
    nose_tip = lm.get("nose_tip") or lm.get("nose_bridge")
    if not left_eye or not right_eye:
        return None

    pts = np.zeros((68, 2), dtype=np.float32)
    left_eye_pts = np.array(left_eye, dtype=np.float32)
    right_eye_pts = np.array(right_eye, dtype=np.float32)

    if left_eye_pts.shape[0] >= 6:
        pts[36:42] = left_eye_pts[:6]
    if right_eye_pts.shape[0] >= 6:
        pts[42:48] = right_eye_pts[:6]
    if nose_tip:
        nose_pts = np.array(nose_tip, dtype=np.float32)
        if nose_pts.ndim == 2 and nose_pts.shape[0] > 0:
            pts[NOSE_TIP_IDX] = nose_pts[min(2, nose_pts.shape[0] - 1)]

    if not np.any(pts[36:48]):
        return None
    return pts


def _ear_from_points(pts):
    """CHANGED: extracted so get_face_metrics() can reuse it without re-deriving landmarks."""
    left_eye = pts[LEFT_EYE_IDX]
    right_eye = pts[RIGHT_EYE_IDX]
    ear = (_eye_aspect_ratio(left_eye) + _eye_aspect_ratio(right_eye)) / 2.0
    if np.isnan(ear) or np.isinf(ear):
        return None
    return float(ear)


def _yaw_from_points(pts):
    """CHANGED: extracted so get_face_metrics() can reuse it without re-deriving landmarks."""
    if not np.any(pts[NOSE_TIP_IDX]):
        return None

    left_eye_center = np.mean(pts[LEFT_EYE_IDX], axis=0)
    right_eye_center = np.mean(pts[RIGHT_EYE_IDX], axis=0)
    nose = pts[NOSE_TIP_IDX]

    dl = float(np.linalg.norm(nose - left_eye_center))
    dr = float(np.linalg.norm(nose - right_eye_center))

    if dl <= 1e-6 or dr <= 1e-6:
        return None

    ratio = dl / dr
    if np.isnan(ratio) or np.isinf(ratio) or ratio <= 1e-12:
        return None

    signed = -float(np.log(ratio))
    if np.isnan(signed) or np.isinf(signed):
        return None

    return signed


def _ear_proxy_without_landmarks(img, face_box):
    """CHANGED: extracted fallback proxy (used when dlib/face_recognition landmarks are unavailable)."""
    x, y, w, h = face_box
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    top = gray[max(0, y):max(0, y + int(h * 0.55)), max(0, x):max(0, x + w)]
    if top.size == 0:
        return None
    eye_band = top[max(0, int(top.shape[0] * 0.12)):max(1, int(top.shape[0] * 0.42)), :]
    if eye_band.size == 0:
        return None
    blurred = cv2.GaussianBlur(eye_band, (5, 5), 0)
    contrast = float(np.std(blurred))
    norm = float(np.mean(blurred)) if float(np.mean(blurred)) > 1e-6 else 1.0
    proxy = 0.35 + (contrast / norm) * 0.08
    return float(max(0.05, min(0.6, proxy)))


def get_ear_from_face(img, face_box):
    """Kept for backward compatibility with any caller that only needs EAR."""
    pts = get_landmarks(img, face_box)
    if pts is None:
        return _ear_proxy_without_landmarks(img, face_box)
    return _ear_from_points(pts)


def yaw_ratio_from_face(img, face_box):
    """Kept for backward compatibility with any caller that only needs yaw."""
    pts = get_landmarks(img, face_box)
    if pts is None:
        return None
    return _yaw_from_points(pts)


def get_face_metrics(img, face_box):
    """
    CHANGED (Fix 1 — performance):
    Computes dlib landmarks ONCE per frame and derives both EAR and yaw from
    that single landmark pass, instead of get_ear_from_face() and
    yaw_ratio_from_face() each independently calling get_landmarks() (which
    runs the expensive dlib shape predictor a second time on the same frame).

    Returns: (ear, yaw) — either may be None if unavailable.
    Use this in any hot loop that previously called both functions separately
    (e.g. liveness sequence frame processing).
    """
    pts = get_landmarks(img, face_box)
    if pts is None:
        # No landmarks available at all — fall back to EAR proxy, no yaw possible.
        return _ear_proxy_without_landmarks(img, face_box), None

    ear = _ear_from_points(pts)
    yaw = _yaw_from_points(pts)
    return ear, yaw