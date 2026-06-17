"""Face embedding and verification JSON API routes."""

from ._shared import _set_liveness_running, load_app_context


load_app_context(globals())


@app.route("/api/face/embed", methods=["POST"])
def api_face_embed():
    """
    CHANGED: Receives a raw image frame (multipart/form-data with key 'frame')
    from the frontend face monitor, runs face detection and embedding generation,
    and returns the 128D embedding as a JSON array.

    CHANGED: Enforces exactly one usable face and generates the embedding
    from the selected face crop only, to reduce wrong-face / background noise.
    Returns { embedding: [128 floats], face_count: int } on success,
    or { embedding: None, face_count: int, error: str } when invalid.
    """
    guard = student_required()
    if guard:
        return fail("Not logged in", 401)

    frame_file = request.files.get("frame")
    if not frame_file:
        return fail("No frame provided", 400)

    try:
        file_bytes = np.frombuffer(frame_file.read(), dtype=np.uint8)
        frame = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        if frame is None:
            return fail("Could not decode image", 400)
    except Exception as e:
        return fail(f"Image decode error: {str(e)}", 400)

    # CHANGED: Detect and filter faces first
    faces_raw = detect_faces(frame)
    filtered = filter_faces(faces_raw, frame.shape)
    face_count = len(filtered)

    # CHANGED: Enforce exactly one face
    if face_count == 0:
        return ok(
            {"embedding": None, "face_count": 0, "error": "No face detected"},
            "No face detected"
        )

    if face_count > 1:
        return ok(
            {"embedding": None, "face_count": face_count, "error": "Multiple faces detected"},
            "Multiple faces detected"
        )

    # CHANGED: Use the selected single face box
    face_box, err = pick_single_face(filtered, frame)
    if err is not None or face_box is None:
        return ok(
            {"embedding": None, "face_count": face_count, "error": err or "Face selection failed"},
            err or "Face selection failed"
        )

    x, y, w, h = face_box

    # CHANGED: Add margin around detected face
    pad_x = int(w * 0.20)
    pad_y = int(h * 0.20)

    x1 = max(0, x - pad_x)
    y1 = max(0, y - pad_y)
    x2 = min(frame.shape[1], x + w + pad_x)
    y2 = min(frame.shape[0], y + h + pad_y)

    face_crop = frame[y1:y2, x1:x2]

    # CHANGED: Reject invalid/too-small crop
    if face_crop is None or face_crop.size == 0:
        return ok(
            {"embedding": None, "face_count": face_count, "error": "Invalid face crop"},
            "Invalid face crop"
        )

    if face_crop.shape[0] < 40 or face_crop.shape[1] < 40:
        return ok(
            {"embedding": None, "face_count": face_count, "error": "Face too small"},
            "Face too small"
        )

    # CHANGED: Generate embedding from cropped face only
    emb, err = generate_embedding(face_crop)
    if err:
        return ok(
            {"embedding": None, "face_count": face_count, "error": err},
            err
        )

    emb_list = np.asarray(emb, dtype=np.float32).reshape(-1).tolist()
    if len(emb_list) != 128:
        return fail("Invalid embedding length", 400)

    return ok(
        {"embedding": emb_list, "face_count": face_count},
        "Embedding generated"
    )


@app.route("/api/face/verify", methods=["POST"])
def api_face_verify():
    """
    CHANGED: Now uses multi-embedding database where each user maps to a LIST
    of embeddings. The live embedding is compared against all stored embeddings
    per user and the best (lowest) distance is used per user.

    CHANGED: Uses face_match_passes_85() instead of only checking calibrated
    confidence. This keeps this verification endpoint aligned with the stricter
    quiz-entry verification rule, including the hard maximum distance.
    """
    data = request.get_json()
    if not data or "embedding" not in data:
        return fail("Missing embedding", 400)

    embedding = data["embedding"]
    if not isinstance(embedding, list) or len(embedding) != 128:
        return fail("Invalid embedding format", 400)

    # CHANGED: Build database now returns { name: [list of 128D embeddings] }
    database = build_database_from_pg_and_firebase(limit_users=500)
    if not database:
        return fail("No registered students with embeddings", 400)

    # CHANGED:
    # Find the best candidate user, but the candidate still needs majority approval.
    # A user only passes if at least 3 of their stored embeddings agree.
    best_name = None
    best_distance = 999.0
    best_match_info = None

    for name, emb_list_of_lists in database.items():
        if not isinstance(emb_list_of_lists, list):
            continue

        match_info = face_match_passes_majority(
            embedding,
            emb_list_of_lists,
            min_match_count=FACE_VERIFY_MIN_MATCH_COUNT,
        )

        dist = match_info["best_distance"]
        if dist < best_distance:
            best_distance = dist
            best_name = name
            best_match_info = match_info

    if best_match_info is None:
        best_match_info = {
            "matched": False,
            "confidence": 0.0,
            "best_distance": 999.0,
            "matched_count": 0,
            "required_match_count": FACE_VERIFY_MIN_MATCH_COUNT,
            "distance_debug": [],
        }

    matched = best_match_info["matched"]
    confidence = best_match_info["confidence"]
    matched_count = best_match_info["matched_count"]
    required_match_count = best_match_info["required_match_count"]
    distance_debug = best_match_info["distance_debug"]

    if best_name and matched:
        return ok(
            {
                "status": "verified",
                "name": best_name,
                "distance": round(float(best_distance), 4),
                "matched_count": matched_count,
                "required_match_count": required_match_count,
                "all_distances": distance_debug,
                "confidence": round(float(confidence), 4),
                "confidence_percent": round(float(confidence) * 100, 2),
                "threshold_percent": int(FACE_VERIFY_CONFIDENCE_THRESHOLD * 100),
            },
            "Match found",
        )

    return ok(
        {
            "status": "unknown",
            "distance": round(float(best_distance), 4),
            "matched_count": matched_count,
            "required_match_count": required_match_count,
            "all_distances": distance_debug,
            "confidence": round(float(confidence), 4),
            "confidence_percent": round(float(confidence) * 100, 2),
            "threshold_percent": int(FACE_VERIFY_CONFIDENCE_THRESHOLD * 100),
        },
        "No match",
    )
