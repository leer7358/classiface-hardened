import cv2
import numpy as np

from detection.face_matching import detect_faces

try:
    import face_recognition
except ImportError:
    face_recognition = None


def _opencv_embedding(frame, face_box):
    if face_box is None:
        face = frame
    else:
        x, y, w, h = face_box
        face = frame[max(0, y):max(0, y + h), max(0, x):max(0, x + w)]
    if face.size == 0:
        return None
    gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (16, 8), interpolation=cv2.INTER_AREA)
    emb = resized.astype(np.float32).reshape(-1) / 255.0
    norm = np.linalg.norm(emb)
    return emb / norm if norm > 1e-6 else emb


def generate_embedding(frame):
    """
    Detect a face and produce a 128D embedding
    """
    if face_recognition is None:
        faces = detect_faces(frame)
        face_box = None
        if len(faces) == 1:
            face_box = faces[0]
        elif len(faces) > 1:
            faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
            largest = faces[0][2] * faces[0][3]
            second = faces[1][2] * faces[1][3]
            if largest < (second * 2.5):
                return None, "Multiple faces detected"
            face_box = faces[0]

        embedding = _opencv_embedding(frame, face_box)
        if embedding is None:
            return None, "Encoding failed"
        return embedding, None

    rgb = frame[:, :, ::-1]  # Convert BGR (opencv) -> RGB

    locations = face_recognition.face_locations(rgb)

    if len(locations) == 0:
        embedding = _opencv_embedding(frame, None)
        if embedding is not None:
            return embedding, None
        return None, "No face detected"

    if len(locations) > 1:
        return None, "Multiple faces detected"

    encodings = face_recognition.face_encodings(rgb, locations)

    if len(encodings) == 0:
        embedding = _opencv_embedding(frame, None)
        if embedding is not None:
            return embedding, None
        return None, "Encoding failed"

    return encodings[0], None


def compare_with_database(embedding, database, threshold=0.5):
    """
    Compare embedding against stored embeddings
    Returns:
        (name_or_none, distance, confidence)
    """

    if not database:
        return None, 999.0, 0.0

    names = list(database.keys())
    stored_embeddings = np.array(list(database.values()))

    if face_recognition is not None:
        distances = face_recognition.face_distance(stored_embeddings, embedding)
    else:
        distances = np.linalg.norm(stored_embeddings - embedding, axis=1)
    best_index = int(np.argmin(distances))
    best_distance = float(distances[best_index])

    # Convert distance to confidence (0–1 scale)
    # Smooth exponential mapping
    confidence = float(np.exp(-best_distance * 5.0))
    confidence = max(0.0, min(1.0, confidence))

    if best_distance < threshold:
        return names[best_index], best_distance, confidence

    return None, best_distance, confidence
