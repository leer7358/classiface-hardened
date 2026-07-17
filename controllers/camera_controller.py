"""Camera streaming, registration capture, and capture status routes."""

from ._shared import _set_liveness_running, load_app_context


load_app_context(globals())



def _pending_monitor_store_key(ts_key: str) -> str:
    """
    CHANGED: Legacy combined temporary key for monitoring-support embeddings.
    Front registration embeddings still use the normal ts_key.
    """
    return f"{str(ts_key)}:monitor"


def _pending_monitor_pose_store_key(ts_key: str, pose: str) -> str:
    """
    CHANGED:
    Separate temporary key for pose-aware monitoring embeddings.

    Added ``screen_front`` so registration can keep a natural quiz-posture
    sample for continuous monitoring only. Strict quiz verification still uses
    the normal frontal identity key, not this monitoring-support key.
    """
    pose = str(pose or "").strip().lower()
    if pose not in ("left", "right", "screen_front"):
        pose = "front"
    return f"{str(ts_key)}:monitor:{pose}"


def _registration_quality_retry_message(reason: str) -> str:
    """
    CHANGED:
    Convert backend enrolment quality reasons into clear user guidance.

    This is an IT/user-support layer only. It does not change face distance,
    confidence thresholds, environment variables, or deployment settings.
    """
    reason_key = str(reason or "").strip().lower()

    if "dark" in reason_key or "brightness_low" in reason_key:
        return "Face is too dark. Please improve lighting and try again."
    if "bright" in reason_key or "overexposed" in reason_key or "brightness_high" in reason_key:
        return "Face is too bright. Please reduce lighting and try again."
    if "contrast" in reason_key:
        return "Face has low contrast. Please adjust lighting and try again."
    if "blur" in reason_key or "sharp" in reason_key:
        return "Image is blurry. Please hold still and try again."
    if "small" in reason_key:
        return "Face is too small. Please move closer and try again."
    if "close" in reason_key or "large" in reason_key:
        return "Face is too close. Please move back slightly and try again."
    if "center" in reason_key or "centre" in reason_key:
        return "Please centre your face in the guide frame and try again."

    return "Enrollment image quality is weak. Please follow the guide and try again."


def _registration_front_sample_error(frame, face_box):
    """
    CHANGED:
    Final backend approval check for front-facing registration samples.

    The frontend guides the student, but the backend still decides whether the
    submitted sample is acceptable before it is saved as an identity reference.
    This keeps quiz-entry references front-facing without changing recognition
    distance/confidence thresholds or deployment settings.
    """
    metrics = {}

    try:
        x, y, w, h = face_box
        frame_h, frame_w = frame.shape[:2]
        frame_area = max(1.0, float(frame_w * frame_h))
        face_ratio = float(w * h) / frame_area
        center_x = (float(x) + (float(w) / 2.0)) / max(1.0, float(frame_w))
        center_y = (float(y) + (float(h) / 2.0)) / max(1.0, float(frame_h))
        center_offset_x = abs(center_x - 0.5)
        center_offset_y = abs(center_y - 0.5)

        metrics.update({
            "face_ratio": round(face_ratio, 4),
            "center_x": round(center_x, 4),
            "center_y": round(center_y, 4),
            "center_offset_x": round(center_offset_x, 4),
            "center_offset_y": round(center_offset_y, 4),
        })

        # Registration quality controls only. These do not change face-matching
        # thresholds. Defaults are deliberately practical and can be overridden
        # by existing app config/globals if present.
        min_face_ratio = float(globals().get("ENROLLMENT_MIN_FACE_RATIO", 0.08))
        max_face_ratio = float(globals().get("ENROLLMENT_MAX_FACE_RATIO", 0.38))
        max_center_offset_x = float(globals().get("ENROLLMENT_MAX_CENTER_OFFSET_X", 0.12))
        max_center_offset_y = float(globals().get("ENROLLMENT_MAX_CENTER_OFFSET_Y", 0.10))

        if face_ratio < min_face_ratio:
            return "Face is too small. Please move closer and try again.", metrics

        if face_ratio > max_face_ratio:
            return "Face is too close. Please move back slightly and try again.", metrics

        if center_offset_x > max_center_offset_x or center_offset_y > max_center_offset_y:
            if center_offset_y > max_center_offset_y:
                if center_y < 0.5:
                    return "Please move your face slightly lower so it is centred in the guide frame.", metrics
                return "Please move your face slightly higher so it is centred in the guide frame.", metrics
            if center_x < 0.5:
                return "Please move your face slightly to the right so it is centred in the guide frame.", metrics
            return "Please move your face slightly to the left so it is centred in the guide frame.", metrics
    except Exception as box_err:
        metrics["box_check_error"] = type(box_err).__name__

    quality_checker = globals().get("_sample_frame_quality_metrics")
    if callable(quality_checker):
        try:
            quality_metrics = quality_checker(frame, face_box) or {}
            metrics.update(quality_metrics)
        except Exception as quality_err:
            print(
                f"[FRONT-EMBEDDING] quality_check_skipped={type(quality_err).__name__}",
                flush=True,
            )
            quality_metrics = {}

        if quality_metrics and not quality_metrics.get("quality_ok", False):
            reason = quality_metrics.get("quality_reason")
            return _registration_quality_retry_message(reason), metrics

    try:
        yaw = yaw_ratio_from_face(frame, face_box)
    except Exception:
        yaw = None

    if yaw is not None:
        try:
            metrics["yaw_ratio"] = round(float(yaw), 4)
        except Exception:
            pass

        # Use the same front-facing tolerance already used by the existing
        # registration controller path.
        if abs(float(yaw)) > 0.20:
            return "Please look directly at the camera and keep your face centred.", metrics

    return None, metrics



def _registration_consistency_fail_session_key(ts_key: str) -> str:
    """
    CHANGED:
    Session key for consecutive registration consistency failures.

    This lets the system reject only the bad capture first, while keeping the
    already-approved front sample. If the same registration attempt repeatedly
    fails, the attempt is reset as a stronger safety control.
    """
    safe_key = str(ts_key or "").strip() or "unknown"
    return f"registration_consistency_fail_count:{safe_key}"


def _registration_clear_consistency_failures(ts_key: str) -> None:
    """
    CHANGED:
    Clear the consecutive consistency-failure counter after a successful
    front-sample save or a full registration reset.
    """
    try:
        session.pop(_registration_consistency_fail_session_key(ts_key), None)
        session.modified = True
    except Exception:
        pass


def _registration_note_consistency_failure(ts_key: str, metrics: dict):
    """
    CHANGED:
    Track repeated registration consistency failures.

    Behaviour:
    - First few failures: reject only the new bad capture and keep existing
      approved front samples for recapture.
    - Repeated failures: reset the whole registration attempt to protect
      against mixed-person enrolment attempts.
    """
    try:
        max_soft_fails = int(globals().get("REGISTRATION_CONSISTENCY_MAX_SOFT_FAILS", 3))
    except Exception:
        max_soft_fails = 3

    max_soft_fails = max(1, max_soft_fails)

    fail_key = _registration_consistency_fail_session_key(ts_key)
    try:
        fail_count = int(session.get(fail_key) or 0) + 1
    except Exception:
        fail_count = 1

    try:
        session[fail_key] = fail_count
        session.modified = True
    except Exception:
        pass

    should_reset = fail_count >= max_soft_fails

    print(
        f"[REGISTRATION-CONSISTENCY] recapture_decision "
        f"fail_count={fail_count} max_soft_fails={max_soft_fails} "
        f"should_reset={should_reset} metrics={metrics}",
        flush=True,
    )

    return bool(should_reset), int(fail_count), int(max_soft_fails)


def _registration_consistency_recapture_message(metrics: dict, fail_count: int = 1, max_soft_fails: int = 3) -> str:
    """
    CHANGED:
    User-facing message when one capture is rejected but existing approved
    samples are kept.
    """
    try:
        best_distance = (metrics or {}).get("best_distance")
        distance_boundary = (metrics or {}).get("distance_boundary")
        distance_text = ""
        if best_distance is not None and distance_boundary is not None:
            distance_text = f" Distance was {best_distance}, expected within {distance_boundary}."
    except Exception:
        distance_text = ""

    return (
        "This capture did not match your previous approved registration sample. "
        "Your previous sample was kept. Please recapture with the same person, "
        "same distance, good lighting, and face straight to the camera."
        + distance_text
    )



def _registration_clear_pending_samples(ts_key: str) -> None:
    """
    CHANGED:
    Clear all temporary registration samples for the current enrolment attempt.

    This is used when the backend detects that the 5 strict front samples are
    no longer consistent. It prevents mixed identity samples from being saved.
    """
    if not ts_key:
        return

    keys_to_clear = [
        str(ts_key),
        _pending_monitor_store_key(ts_key),
        _pending_monitor_pose_store_key(ts_key, "screen_front"),
        _pending_monitor_pose_store_key(ts_key, "left"),
        _pending_monitor_pose_store_key(ts_key, "right"),
    ]

    for clear_key in keys_to_clear:
        try:
            _pending_store_pop(clear_key)
        except Exception as clear_err:
            print(
                f"[REGISTRATION-CONSISTENCY] pending_clear_skipped key={clear_key} "
                f"error={type(clear_err).__name__}",
                flush=True,
            )

    try:
        _registration_clear_consistency_failures(ts_key)
        session.pop("ts", None)
        session.modified = True
    except Exception:
        pass


def _registration_identity_consistency_error(ts_key: str, new_embedding: list):
    """
    CHANGED:
    Enrolment consistency control for strict front identity samples.

    Purpose:
    - The first accepted front sample becomes the temporary identity reference.
    - Each next front sample must be consistent with the already accepted front samples.
    - This prevents 2 samples from one person and 3 samples from another person
      from completing one registration.

    This is a registration data-integrity control only. It does not change the
    face recognition model, distance formula, quiz verification threshold, or
    deployment settings.
    """
    metrics = {
        "existing_front_samples": 0,
        "matched_existing_samples": 0,
        "required_existing_matches": 0,
        "best_distance": None,
        "distance_boundary": float(globals().get("FACE_VERIFY_HARD_MAX_DISTANCE", 0.25)),
    }

    if not ts_key:
        return None, metrics

    existing_embeddings = _pending_store_get(ts_key) or []
    existing_embeddings = [
        emb for emb in existing_embeddings
        if isinstance(emb, list) and len(emb) == 128
    ]

    metrics["existing_front_samples"] = len(existing_embeddings)

    # First sample becomes the temporary registration anchor.
    if not existing_embeddings:
        return None, metrics

    distances = []
    for stored_emb in existing_embeddings:
        try:
            if callable(globals().get("_face_distance")):
                dist = float(_face_distance(new_embedding, stored_emb))
            else:
                new_arr = np.asarray(new_embedding, dtype=np.float32).reshape(-1)
                old_arr = np.asarray(stored_emb, dtype=np.float32).reshape(-1)
                if new_arr.size != 128 or old_arr.size != 128:
                    continue
                dist = float(np.linalg.norm(new_arr - old_arr))

            if dist < 999.0:
                distances.append(dist)
        except Exception:
            continue

    distances.sort()

    if not distances:
        return "Could not confirm registration sample consistency. Please try again.", metrics

    distance_boundary = float(metrics["distance_boundary"])
    matched_existing = sum(1 for dist in distances if dist <= distance_boundary)

    # Sample 2 must match sample 1.
    # From sample 3 onwards, require at least two existing approved samples to match.
    # This avoids one lucky low-distance frame from completing a mixed registration.
    required_existing_matches = 1 if len(existing_embeddings) == 1 else 2

    metrics.update({
        "matched_existing_samples": int(matched_existing),
        "required_existing_matches": int(required_existing_matches),
        "best_distance": round(float(distances[0]), 4),
        "distances": [round(float(dist), 4) for dist in distances[:5]],
    })

    if matched_existing < required_existing_matches:
        return (
            "Registration samples are inconsistent. Please restart registration "
            "and make sure only one person completes the entire process."
        ), metrics

    return None, metrics

def _registration_side_pose_error(frame, face_box, expected_pose: str):
    """
    CHANGED:
    Backend safeguard for left/right monitoring-support samples.

    The progress bar guides the student in the browser, but this check confirms
    that the side-support sample matches the expected left/right pose before it
    is stored. Side samples remain monitoring support only.
    """
    pose = str(expected_pose or "").strip().lower()
    if pose not in ("left", "right"):
        return "invalid_pose", {}

    try:
        yaw = yaw_ratio_from_face(frame, face_box)
    except Exception as yaw_err:
        return None, {"yaw_error": type(yaw_err).__name__}

    if yaw is None:
        return None, {"yaw_ratio": None}

    try:
        yaw_value = float(yaw)
    except Exception:
        return None, {"yaw_ratio": None}

    side_min = float(globals().get("WS_MONITOR_SIDE_POSE_YAW_MIN", 0.055))
    left_sign = int(globals().get("LIVENESS_LEFT_YAW_SIGN", 1))
    expected_sign = left_sign if pose == "left" else -left_sign
    actual_sign = 1 if yaw_value > 0 else -1 if yaw_value < 0 else 0

    metrics = {
        "yaw_ratio": round(yaw_value, 4),
        "expected_pose": pose,
        "expected_sign": expected_sign,
        "actual_sign": actual_sign,
    }

    if abs(yaw_value) < side_min:
        return "not_enough_turn", metrics

    if actual_sign != expected_sign:
        return "wrong_turn_direction", metrics

    return None, metrics


def _registration_screen_front_sample_error(frame, face_box):
    """
    CHANGED:
    Backend approval check for natural screen-facing monitoring samples.

    Unlike strict frontal identity samples, this sample is not used for quiz
    verification or re-verification. It is monitoring support only, so the
    check focuses on one clear, centred, usable face while allowing the student
    to look naturally at the screen.
    """
    if frame is None or face_box is None:
        return "No clear face detected for natural quiz posture.", {}

    metrics = {}

    try:
        x, y, w, h = face_box
        frame_h, frame_w = frame.shape[:2]
        frame_area = max(1.0, float(frame_w * frame_h))
        face_ratio = float(w * h) / frame_area
        center_x = (float(x) + (float(w) / 2.0)) / max(1.0, float(frame_w))
        center_y = (float(y) + (float(h) / 2.0)) / max(1.0, float(frame_h))
        center_offset_x = abs(center_x - 0.5)
        center_offset_y = abs(center_y - 0.5)

        metrics.update({
            "face_ratio": round(face_ratio, 4),
            "center_x": round(center_x, 4),
            "center_y": round(center_y, 4),
            "center_offset_x": round(center_offset_x, 4),
            "center_offset_y": round(center_offset_y, 4),
        })

        min_face_ratio = float(globals().get("ENROLLMENT_MIN_FACE_RATIO", 0.08))
        max_face_ratio = float(globals().get("ENROLLMENT_MAX_FACE_RATIO", 0.38))
        max_center_offset_x = float(globals().get("ENROLLMENT_MAX_CENTER_OFFSET_X", 0.12))
        max_center_offset_y = float(globals().get("ENROLLMENT_MAX_CENTER_OFFSET_Y", 0.10))

        if face_ratio < min_face_ratio:
            return "Face is too small during natural quiz posture. Please move closer.", metrics
        if face_ratio > max_face_ratio:
            return "Face is too close during natural quiz posture. Please move back slightly.", metrics
        if center_offset_x > max_center_offset_x or center_offset_y > max_center_offset_y:
            return "Please keep your face centred while looking at the screen naturally.", metrics

    except Exception as box_err:
        metrics["box_check_error"] = type(box_err).__name__

    quality_checker = globals().get("_sample_frame_quality_metrics")
    if callable(quality_checker):
        try:
            quality_metrics = quality_checker(frame, face_box) or {}
            metrics.update(quality_metrics)
        except Exception as quality_err:
            print(
                f"[SCREEN-FRONT-SUPPORT] quality_check_skipped={type(quality_err).__name__}",
                flush=True,
            )
            quality_metrics = {}

        if quality_metrics and not quality_metrics.get("quality_ok", False):
            reason = quality_metrics.get("quality_reason")
            return _registration_quality_retry_message(reason), metrics

    # Do not require a strict camera-facing yaw here. This sample represents
    # the student looking at the screen during the quiz, not identity entry.
    try:
        yaw = yaw_ratio_from_face(frame, face_box)
    except Exception:
        yaw = None

    if yaw is not None:
        try:
            metrics["yaw_ratio"] = round(float(yaw), 4)
        except Exception:
            pass

        max_natural_yaw = float(globals().get("SCREEN_FRONT_MAX_YAW_RATIO", 0.28))
        if abs(float(yaw)) > max_natural_yaw:
            return "Please keep your face mostly visible while looking at the screen.", metrics

    return None, metrics


def _extract_liveness_phase_frames(sequence_payload, phase_names, max_frames=18):
    """
    CHANGED:
    Extract decoded frames for a named browser liveness phase.

    The frontend sends compacted liveness frames for all phases. This helper
    filters the requested phase and decodes only the frames needed by the
    controller.
    """
    phase_set = {str(name or "").strip().lower() for name in (phase_names or [])}
    frames_payload = []

    try:
        if isinstance(sequence_payload, dict):
            frames_payload = sequence_payload.get("frames") or []
        elif isinstance(sequence_payload, str):
            parsed = json.loads(sequence_payload)
            if isinstance(parsed, dict):
                frames_payload = parsed.get("frames") or []
            elif isinstance(parsed, list):
                frames_payload = parsed
        elif isinstance(sequence_payload, list):
            frames_payload = sequence_payload
    except Exception:
        frames_payload = []

    decoded_frames = []
    for item in frames_payload or []:
        if not isinstance(item, dict):
            continue

        item_phase = str(item.get("phase") or "").strip().lower()
        if phase_set and item_phase not in phase_set:
            continue

        image_data = item.get("image") or item.get("frame_data") or item.get("data")
        if not image_data:
            continue

        frame, decode_err = decode_browser_frame(image_data)
        if decode_err or frame is None:
            continue

        decoded_frames.append(frame)

    if max_frames and len(decoded_frames) > max_frames:
        step = (len(decoded_frames) - 1) / max(1, max_frames - 1)
        decoded_frames = [decoded_frames[round(idx * step)] for idx in range(max_frames)]

    return decoded_frames


def _store_monitor_screen_front_support_embeddings(ts_key: str, state: dict) -> int:
    """
    CHANGED:
    Store natural screen-facing front samples for continuous monitoring only.

    These samples are saved to the monitoring-support temporary store and must
    not be saved to the strict front identity list used by quiz verification.
    """
    if not ts_key:
        return 0

    frontal_embeddings = _pending_store_get(ts_key) or []
    front_count = len(frontal_embeddings)
    # CHANGED:
    # Save natural screen-facing monitoring support from capture 1/5 through 5/5.
    # The strict identity samples are still controlled by the normal front
    # registration list. Screen-front samples remain monitoring-only.
    if front_count <= 0:
        print(
            f"[SCREEN-FRONT-SUPPORT] skipped reason=no_front_refs "
            f"front_refs={front_count}",
            flush=True,
        )
        return 0

    screen_frames = (state or {}).get("screen_monitor_frames") or []
    if not isinstance(screen_frames, list) or not screen_frames:
        print("[SCREEN-FRONT-SUPPORT] skipped reason=no_screen_front_frames", flush=True)
        return 0

    screen_ts_key = _pending_monitor_pose_store_key(ts_key, "screen_front")
    existing_count = _pending_store_get_count(screen_ts_key)
    max_screen_count = int(REGISTRATION_SAMPLE_COUNT)
    if existing_count >= max_screen_count:
        return 0

    saved_support_count = 0
    for screen_frame in screen_frames:
        if existing_count + saved_support_count >= max_screen_count:
            break

        face_crop, face_box, crop_err = prepare_face_crop_from_frame(screen_frame, pad_ratio=0.20)
        if crop_err:
            print(
                f"[SCREEN-FRONT-SUPPORT] skipped crop_err={crop_err}",
                flush=True,
            )
            continue

        screen_error, screen_metrics = _registration_screen_front_sample_error(screen_frame, face_box)
        if screen_error:
            print(
                f"[SCREEN-FRONT-SUPPORT] skipped reason={screen_error} metrics={screen_metrics}",
                flush=True,
            )
            continue

        emb, err = generate_embedding(face_crop)
        if err:
            print(
                f"[SCREEN-FRONT-SUPPORT] skipped embedding_err={err}",
                flush=True,
            )
            continue

        emb_list = np.asarray(emb, dtype=np.float32).reshape(-1).tolist()
        if len(emb_list) != 128:
            print(
                f"[SCREEN-FRONT-SUPPORT] skipped invalid_len={len(emb_list)}",
                flush=True,
            )
            continue

        try:
            screen_distance = _best_distance_against_embeddings(emb_list, frontal_embeddings)
        except Exception:
            screen_distance = 999.0

        max_screen_distance = float(globals().get(
            "ENROLLMENT_SCREEN_FRONT_MAX_DISTANCE_TO_FRONT",
            0.30,
        ))
        if float(screen_distance) > max_screen_distance:
            print(
                f"[SCREEN-FRONT-SUPPORT] skipped reason=too_far_from_front "
                f"distance_to_front={float(screen_distance):.4f} "
                f"max_distance={max_screen_distance:.4f} "
                f"front_refs={front_count}",
                flush=True,
            )
            continue

        _pending_store_put(screen_ts_key, emb_list)
        _pending_store_put(_pending_monitor_store_key(ts_key), emb_list)
        saved_support_count += 1

        print(
            f"[SCREEN-FRONT-SUPPORT] saved "
            f"distance_to_front={float(screen_distance):.4f} "
            f"screen_progress={existing_count + saved_support_count}/{max_screen_count} "
            f"front_refs={front_count}",
            flush=True,
        )

        # CHANGED:
        # Continue through the collected screen-front frames until the monitoring
        # support bank reaches REGISTRATION_SAMPLE_COUNT. This allows one
        # successful registration flow to collect up to five natural posture
        # support samples automatically.

    return saved_support_count




@app.route("/api/registration/quality-guide", methods=["POST"])
def api_registration_quality_guide():
    """
    CHANGED:
    Browser-to-backend registration quality guide.

    The browser FaceDetector API can miss a visible face on some devices or
    browsers. This endpoint lets the frontend send a small preview frame to
    the backend so the same server-side face detection path can provide clearer
    guidance before the user presses Capture.

    This is a registration guidance / input validation endpoint only. It does
    not change face-matching distance values, quiz verification confidence,
    environment variables, or deployment settings.
    """
    if not _require_csrf_json():
        return fail("CSRF failed", 400)

    data = request.get_json(silent=True) or {}
    frame_data = data.get("frame_data") or data.get("image") or ""

    frame, decode_err = decode_browser_frame(frame_data)
    if decode_err or frame is None:
        return ok(
            {
                "quality_ok": False,
                "face_detected": False,
                "status": "warn",
                "label": "CAMERA",
                "message": decode_err or "Camera frame was not readable. Please wait for the preview.",
            },
            "Registration quality guide",
        )

    faces_raw = detect_faces(frame)
    face_box, face_err = pick_single_face(faces_raw, frame)

    if face_err or face_box is None:
        face_err_text = str(face_err or "").lower()
        label = "POSITION FACE"
        message = "Place your full face inside the guide frame. Move back slightly if your face is too close."

        if "multiple" in face_err_text:
            label = "ONE FACE ONLY"
            message = "Only one face should be visible during registration."
        elif "no face" in face_err_text:
            label = "POSITION FACE"
            message = "Place your full face inside the guide frame. Move back slightly if your face is cropped."

        return ok(
            {
                "quality_ok": False,
                "face_detected": False,
                "status": "warn",
                "label": label,
                "message": message,
                "reason": face_err or "no_face",
            },
            "Registration quality guide",
        )

    x, y, w, h = face_box
    frame_h, frame_w = frame.shape[:2]
    frame_area = max(1, frame_h * frame_w)
    face_ratio = (w * h) / frame_area
    center_x = (x + (w / 2.0)) / max(1, frame_w)
    center_y = (y + (h / 2.0)) / max(1, frame_h)
    x_offset = abs(center_x - 0.5)
    y_offset = abs(center_y - 0.5)

    metrics = {
        "face_ratio": round(float(face_ratio), 4),
        "center_x": round(float(center_x), 4),
        "center_y": round(float(center_y), 4),
        "x_offset": round(float(x_offset), 4),
        "y_offset": round(float(y_offset), 4),
    }

    # These are enrolment guidance/acceptance checks, not face-matching
    # thresholds. They keep the captured identity reference usable.
    if face_ratio < 0.08:
        return ok({
            "quality_ok": False,
            "face_detected": True,
            "status": "warn",
            "label": "MOVE CLOSER",
            "message": "Your face is too small. Move closer to the camera.",
            "metrics": metrics,
        }, "Registration quality guide")

    if face_ratio > 0.38:
        return ok({
            "quality_ok": False,
            "face_detected": True,
            "status": "warn",
            "label": "MOVE BACK",
            "message": "Your face is too close. Move back slightly until your full face fits inside the guide.",
            "metrics": metrics,
        }, "Registration quality guide")

    if x_offset > 0.12 or y_offset > 0.10:
        centre_message = "Move your face to the centre of the guide frame."
        if y_offset > 0.10:
            centre_message = (
                "Move your face slightly lower so it is centred in the guide frame."
                if center_y < 0.5
                else "Move your face slightly higher so it is centred in the guide frame."
            )
        elif x_offset > 0.12:
            centre_message = (
                "Move your face slightly to the right so it is centred in the guide frame."
                if center_x < 0.5
                else "Move your face slightly to the left so it is centred in the guide frame."
            )
        return ok({
            "quality_ok": False,
            "face_detected": True,
            "status": "warn",
            "label": "CENTRE FACE",
            "message": centre_message,
            "metrics": metrics,
        }, "Registration quality guide")

    quality_checker = globals().get("_sample_frame_quality_metrics")
    if callable(quality_checker):
        try:
            quality = quality_checker(frame, face_box) or {}
        except Exception as quality_err:
            quality = {"quality_ok": True, "quality_error": type(quality_err).__name__}

        if quality and quality.get("quality_ok") is False:
            reason = quality.get("quality_reason") or "weak_quality"
            metrics.update({
                "quality_reason": reason,
                "blur": round(float(quality.get("blur") or 0), 2),
                "brightness": round(float(quality.get("brightness") or 0), 2),
                "contrast": round(float(quality.get("contrast") or 0), 2),
            })
            return ok({
                "quality_ok": False,
                "face_detected": True,
                "status": "warn",
                "label": "ADJUST QUALITY",
                "message": _registration_quality_retry_message(reason),
                "metrics": metrics,
            }, "Registration quality guide")

    try:
        yaw = yaw_ratio_from_face(frame, face_box)
    except Exception:
        yaw = None

    if yaw is not None:
        metrics["yaw_ratio"] = round(float(yaw), 4)
        if abs(float(yaw)) > 0.20:
            return ok({
                "quality_ok": False,
                "face_detected": True,
                "status": "warn",
                "label": "LOOK STRAIGHT",
                "message": "Look directly at the camera before pressing Capture.",
                "metrics": metrics,
            }, "Registration quality guide")

    return ok({
        "quality_ok": True,
        "face_detected": True,
        "status": "good",
        "label": "READY",
        "message": "Good position. Keep your full face centred inside the guide, then press Capture.",
        "metrics": metrics,
    }, "Registration quality guide")

def _store_monitor_side_support_embeddings(ts_key: str, state: dict) -> int:
    """
    CHANGED:
    Stores left/right monitoring-support embeddings separately.

    Why:
    - A left-turned live face should be compared with left-turn samples.
    - A right-turned live face should be compared with right-turn samples.
    - Strict quiz entry and re-verification still use frontal embeddings only.

    A single registration liveness flow may now provide several left/right
    frames. The backend stores up to REGISTRATION_SAMPLE_COUNT samples per pose,
    but each support frame is still checked for one clear face, usable quality,
    and expected pose before it is saved.
    """
    if not ts_key:
        return 0

    frontal_embeddings = _pending_store_get(ts_key) or []
    front_count = len(frontal_embeddings)
    if front_count <= 0:
        print(
            f"[POSE-SUPPORT-EMBEDDING] skipped reason=no_front_refs "
            f"front_refs={front_count}",
            flush=True,
        )
        return 0

    state = state or {}
    side_frames_single = state.get("side_enrollment_frames") or {}
    if not isinstance(side_frames_single, dict):
        side_frames_single = {}

    max_side_per_pose = int(REGISTRATION_SAMPLE_COUNT)
    saved_support_count = 0

    for pose in ("left", "right"):
        pose_ts_key = _pending_monitor_pose_store_key(ts_key, pose)
        existing_pose_count = _pending_store_get_count(pose_ts_key)
        if existing_pose_count >= max_side_per_pose:
            continue

        # CHANGED:
        # Prefer the full decoded phase frame list collected by
        # /api/liveness/validate-phase. Fall back to the one selected
        # side_enrollment_frame from the shared liveness state for compatibility.
        candidate_frames = state.get(f"{pose}_monitor_frames") or []
        if not isinstance(candidate_frames, list):
            candidate_frames = []

        fallback_frame = side_frames_single.get(pose)
        if fallback_frame is not None:
            candidate_frames.append(fallback_frame)

        if not candidate_frames:
            print(
                f"[POSE-SUPPORT-EMBEDDING] pose={pose} skipped reason=missing_frame",
                flush=True,
            )
            continue

        pose_saved_count = 0

        for side_frame in candidate_frames:
            if existing_pose_count + pose_saved_count >= max_side_per_pose:
                break

            face_crop, face_box, crop_err = prepare_face_crop_from_frame(side_frame, pad_ratio=0.20)
            if crop_err:
                print(
                    f"[POSE-SUPPORT-EMBEDDING] pose={pose} skipped crop_err={crop_err}",
                    flush=True,
                )
                continue

            quality_checker = globals().get("_sample_frame_quality_metrics")
            if not callable(quality_checker):
                print(
                    f"[POSE-SUPPORT-EMBEDDING] pose={pose} skipped reason=quality_checker_unavailable",
                    flush=True,
                )
                continue

            try:
                quality = quality_checker(side_frame, face_box) or {}
            except Exception as quality_err:
                print(
                    f"[POSE-SUPPORT-EMBEDDING] pose={pose} skipped "
                    f"quality_error={type(quality_err).__name__}",
                    flush=True,
                )
                continue

            if not quality.get("quality_ok", False):
                print(
                    f"[POSE-SUPPORT-EMBEDDING] pose={pose} skipped "
                    f"quality={quality.get('quality_reason')} "
                    f"blur={float(quality.get('blur') or 0):.1f} "
                    f"brightness={float(quality.get('brightness') or 0):.1f} "
                    f"contrast={float(quality.get('contrast') or 0):.1f}",
                    flush=True,
                )
                continue

            pose_error, pose_metrics = _registration_side_pose_error(side_frame, face_box, pose)
            if pose_error:
                print(
                    f"[POSE-SUPPORT-EMBEDDING] pose={pose} skipped "
                    f"pose_error={pose_error} metrics={pose_metrics}",
                    flush=True,
                )
                continue

            emb, err = generate_embedding(face_crop)
            if err:
                print(
                    f"[POSE-SUPPORT-EMBEDDING] pose={pose} skipped embedding_err={err}",
                    flush=True,
                )
                continue

            emb_list = np.asarray(emb, dtype=np.float32).reshape(-1).tolist()
            if len(emb_list) != 128:
                print(
                    f"[POSE-SUPPORT-EMBEDDING] pose={pose} skipped invalid_len={len(emb_list)}",
                    flush=True,
                )
                continue

            validator = globals().get("side_embedding_is_valid_against_front")
            if callable(validator):
                side_ok, side_distance = validator(emb_list, frontal_embeddings)
            else:
                try:
                    side_distance = _best_distance_against_embeddings(emb_list, frontal_embeddings)
                except Exception:
                    side_distance = 999.0
                max_side_distance = float(globals().get("ENROLLMENT_SIDE_MAX_DISTANCE_TO_FRONT", 0.32))
                side_ok = float(side_distance) <= max_side_distance

            if not side_ok:
                print(
                    f"[POSE-SUPPORT-EMBEDDING] pose={pose} accepted_as_pose_support_despite_distance "
                    f"distance_to_front={float(side_distance):.4f} "
                    f"front_refs={front_count}",
                    flush=True,
                )
            else:
                print(
                    f"[POSE-SUPPORT-EMBEDDING] pose={pose} accepted_as_pose_support "
                    f"distance_to_front={float(side_distance):.4f} "
                    f"front_refs={front_count}",
                    flush=True,
                )

            _pending_store_put(pose_ts_key, emb_list)
            _pending_store_put(_pending_monitor_store_key(ts_key), emb_list)

            pose_saved_count += 1
            saved_support_count += 1
            print(
                f"[POSE-SUPPORT-EMBEDDING] pose={pose} saved "
                f"distance_to_front={float(side_distance):.4f} "
                f"pose_progress={existing_pose_count + pose_saved_count}/{max_side_per_pose} "
                f"front_refs={front_count}",
                flush=True,
            )

    return saved_support_count


@app.route("/camera")
def camera():
    mode = (request.args.get("mode") or "").strip().lower()

    # CHANGED:
    # Plain /camera causes camera.html to default to register mode because the
    # frontend reads the mode from the URL query string. Canonicalise the URL so
    # quiz verification never falls back to the registration camera.
    if not mode:
        pending_quiz_id = str(session.get("pending_quiz_id") or "").strip()
        verified_quiz_id = str(session.get("quiz_verified_quiz_id") or "").strip()
        is_quiz_verified = (
            bool(session.get("quiz_verified"))
            and pending_quiz_id
            and verified_quiz_id == pending_quiz_id
        )

        if session.get("logged_in") and session.get("role") == "student":
            if is_quiz_verified:
                print(
                    f"[CAMERA-REDIRECT-GUARD] plain_camera verified=True quiz_id={pending_quiz_id}",
                    flush=True,
                )
                return redirect(url_for("stud_quiz_session", quiz_id=pending_quiz_id))

            if pending_quiz_id or session.get("camera_mode") == "quiz":
                print(
                    f"[CAMERA-REDIRECT-GUARD] plain_camera -> mode=quiz pending_quiz_id={pending_quiz_id}",
                    flush=True,
                )
                redirect_args = {"mode": "quiz"}
                if (request.args.get("reverify") or "") == "1":
                    redirect_args["reverify"] = "1"
                return redirect(url_for("camera", **redirect_args))

        print("[CAMERA-REDIRECT-GUARD] plain_camera -> mode=register", flush=True)
        return redirect(url_for("camera", mode="register"))

    stream_key = _get_stream_key()
    _reset_liveness_state(stream_key)

    if mode == "register":
        session["camera_mode"] = "register"
        _ensure_csrf_token()
        captures_done = 0  # CHANGED
        ts_key = session.get("ts", "")
        if ts_key:
            captures_done = _pending_store_get_count(ts_key)
        return render_template(
            "camera.html",
            mode=mode,
            captures_done=captures_done,
            captures_required=REGISTRATION_SAMPLE_COUNT,
        )

    if mode != "quiz":
        return redirect(url_for("camera", mode="register"))

    if not session.get("logged_in"):
        return redirect_with_msg("/login", "Please log in first.")

    session["camera_mode"] = "quiz"
    return render_template("camera.html", mode="quiz")

@app.route("/video_feed")
def video_feed():
    cmode = session.get("camera_mode")
    stream_key = _get_stream_key()
    if cmode == "register":
        return Response(gen_frames(stream_key), mimetype="multipart/x-mixed-replace; boundary=frame")

    if not session.get("logged_in"):
        return abort(401)
    if cmode not in ("quiz", "register"):
        return abort(403)
    return Response(gen_frames(stream_key), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/capture", methods=["POST"])
def capture():
    """
    CHANGED: Multi-capture endpoint. Each call captures one face embedding and
    appends it to the pending store list. A single ts_key is established on the
    first capture and reused for subsequent captures in the same registration session.
    The user must complete REGISTRATION_SAMPLE_COUNT captures before registering.
    After the required number of captures, redirects back to /register with progress info.
    """
    global is_liveness_running

    stream_key = _get_stream_key()
    state = _ensure_liveness_state(stream_key)
    frame_data = request.form.get("frame_data") or ""
    liveness_sequence = request.form.get("liveness_sequence") or ""

    if frame_data:
        ok_live, live_frame, live_err = validate_browser_liveness_sequence(liveness_sequence, stream_key)
        if not ok_live or live_frame is None:
            state["live_instruction"] = "Capture blocked"
            state["live_subtext"] = live_err or "Liveness failed"
            return redirect_with_msg("/camera?mode=register", live_err or "Liveness failed. Please try again.")

        ts_key = session.get("ts")
        if not ts_key:
            ts_key = datetime.now().strftime("%Y%m%d%H%M%S%f")
            session["ts"] = ts_key

        existing_count = _pending_store_get_count(ts_key)
        remaining_needed = max(0, REGISTRATION_SAMPLE_COUNT - existing_count)
        if remaining_needed <= 0:
            return redirect("/register?keep=1")

        # CHANGED:
        # One successful browser liveness sequence can provide several clean
        # front-facing frames. Save up to the remaining required strict front
        # identity samples from this single capture flow instead of forcing the
        # student to repeat liveness five separate times.
        #
        # The required count stays REGISTRATION_SAMPLE_COUNT. Each selected frame
        # still passes backend crop, quality, front-pose, and identity-consistency
        # checks before it is accepted.
        samples_to_save = remaining_needed
        enrollment_frames = state.get("enrollment_frames") or []
        candidate_frames = [live_frame]
        candidate_frames.extend(frame for frame in enrollment_frames if frame is not live_frame)

        saved_count = 0
        last_error = None
        last_consistency_error = None
        last_consistency_metrics = None
        skipped_inconsistent_count = 0
        for sample_frame in candidate_frames:
            if saved_count >= samples_to_save:
                break

            face_crop, face_box, crop_err = prepare_face_crop_from_frame(sample_frame, pad_ratio=0.20)
            if crop_err:
                last_error = crop_err
                continue

            # CHANGED: Final controller-level approval check before saving a
            # frontal registration embedding. Front samples are strict identity
            # references, so the backend confirms quality and front-facing pose.
            front_error, front_metrics = _registration_front_sample_error(sample_frame, face_box)
            if front_error:
                last_error = front_error
                print(
                    f"[FRONT-EMBEDDING] skipped reason={front_error} metrics={front_metrics}",
                    flush=True,
                )
                continue

            emb, err = generate_embedding(face_crop)
            if err:
                last_error = "Failed to generate embedding. Please try again."
                continue

            emb_list = np.asarray(emb, dtype=np.float32).reshape(-1).tolist()
            if len(emb_list) != 128:
                last_error = "Invalid embedding length. Please capture again."
                continue

            consistency_error, consistency_metrics = _registration_identity_consistency_error(ts_key, emb_list)
            if consistency_error:
                last_error = consistency_error
                last_consistency_error = consistency_error
                last_consistency_metrics = consistency_metrics
                skipped_inconsistent_count += 1

                print(
                    f"[REGISTRATION-CONSISTENCY] skipped_auto_candidate reason={consistency_error} "
                    f"candidate_index={skipped_inconsistent_count} "
                    f"existing_front_samples={consistency_metrics.get('existing_front_samples')} "
                    f"matched_existing_samples={consistency_metrics.get('matched_existing_samples')} "
                    f"required_existing_matches={consistency_metrics.get('required_existing_matches')} "
                    f"best_distance={consistency_metrics.get('best_distance')} "
                    f"distance_boundary={consistency_metrics.get('distance_boundary')}",
                    flush=True,
                )

                # CHANGED:
                # Automatic registration may submit many usable-looking front
                # frames from one liveness session. A single inconsistent frame
                # should be skipped, not treated as an immediate registration
                # failure. The backend keeps the approved front samples and
                # continues searching for another consistent frame.
                if len(candidate_frames) > 1:
                    continue

                should_reset, fail_count, max_soft_fails = _registration_note_consistency_failure(
                    ts_key,
                    consistency_metrics,
                )
                if should_reset:
                    _registration_clear_pending_samples(ts_key)
                    return redirect_with_msg("/camera?mode=register", consistency_error)

                retry_message = _registration_consistency_recapture_message(
                    consistency_metrics,
                    fail_count=fail_count,
                    max_soft_fails=max_soft_fails,
                )
                state["live_instruction"] = "Recapture needed"
                state["live_subtext"] = "Keep the same person and face straight"
                return redirect_with_msg("/camera?mode=register", retry_message)

            if saved_count == 0:
                cv2.imwrite(os.path.join(RECOG_FOLDER, "recognized.png"), sample_frame)
            _pending_store_put(ts_key, emb_list)
            _registration_clear_consistency_failures(ts_key)
            saved_count += 1

        captures_done_after_save = _pending_store_get_count(ts_key)

        if saved_count > 0:
            print(
                f"[REGISTRATION-AUTO-SELECT] saved_front_this_request={saved_count} "
                f"skipped_inconsistent={skipped_inconsistent_count} "
                f"front_progress={_pending_store_get_count(ts_key)}/{REGISTRATION_SAMPLE_COUNT}",
                flush=True,
            )
            _store_monitor_screen_front_support_embeddings(ts_key, state)
            _store_monitor_side_support_embeddings(ts_key, state)

        if saved_count == 0:
            if last_consistency_error and last_consistency_metrics:
                should_reset, fail_count, max_soft_fails = _registration_note_consistency_failure(
                    ts_key,
                    last_consistency_metrics,
                )
                if should_reset:
                    _registration_clear_pending_samples(ts_key)
                    return redirect_with_msg("/camera?mode=register", last_consistency_error)

                retry_message = _registration_consistency_recapture_message(
                    last_consistency_metrics,
                    fail_count=fail_count,
                    max_soft_fails=max_soft_fails,
                )
                state["live_instruction"] = "Recapture needed"
                state["live_subtext"] = "Keep the same person and face straight"
                return redirect_with_msg("/camera?mode=register", retry_message)

            return redirect_with_msg(
                "/camera?mode=register",
                last_error or "No usable enrollment frame was captured. Please try again.",
            )

        captures_done = _pending_store_get_count(ts_key)
        state["live_instruction"] = "Face capture successful"
        state["live_subtext"] = f"Progress: {captures_done}/{REGISTRATION_SAMPLE_COUNT}"
        return redirect("/register?keep=1")

    cap = _init_camera()
    state["liveness_preview_frame"] = None  # CHANGED

    direction, blinks_required = _new_challenge()

    # CHANGED: Strengthen liveness challenge.
    # Require at least 2 clear blinks so a very quick/accidental eyelid movement
    # is less likely to pass. Keep the random head turn as the main anti-spoofing step.
    try:
        blinks_required = max(2, int(blinks_required or 1))
    except Exception:
        blinks_required = 2

    session["challenge_blinks"] = blinks_required
    session["challenge_text"] = f"Blink {blinks_required} time(s), turn LEFT, then turn RIGHT"

    # CHANGED: Reuse existing ts_key if already started, else create a new one
    ts_key = session.get("ts")
    if not ts_key:
        ts_key = datetime.now().strftime("%Y%m%d%H%M%S%f")
        session["ts"] = ts_key

    _flush_camera(cap, n=12)

    _set_liveness_running(True)
    try:
        state["live_instruction"] = "Starting liveness..."  # CHANGED
        state["live_subtext"] = f"Blink {blinks_required} times + turn {_direction_prompt(direction)}"  # CHANGED
        ok_live, live_frame, reason = pass_liveness_from_camera(cap, direction, blinks_required, stream_key)
    finally:
        _set_liveness_running(False)
        state["liveness_preview_frame"] = None  # CHANGED

    if not ok_live or live_frame is None:
        # CHANGED: Preserve ts_key if samples were already collected — only clear if nothing captured yet
        if not _pending_store_get_count(session.get("ts", "")):
            session.pop("ts", None)
            _delete_preview_file()

        # CHANGED: user-friendly message mapping
        msg = "Capture failed. Please try again."

        if "Left head turn" in str(reason):
            msg = "Turn your head left and hold for a moment."
        elif "Right head turn" in str(reason):
            msg = "Turn your head right and hold for a moment."
        elif "Head turn not detected" in str(reason):
            msg = "Turn your head left, then right, and hold briefly."
        elif "Need" in str(reason):
            msg = "Blink slowly and clearly."
        elif "No face detected" in str(reason):
            msg = "Move closer so your face is visible."
        elif "Multiple faces" in str(reason):
            msg = "Only one person should be in the frame."
        elif "timeout" in str(reason).lower():
            msg = "Too slow. Please try again."

        state["live_instruction"] = "Capture blocked"  # CHANGED
        state["live_subtext"] = msg  # CHANGED
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/camera?mode=register", msg)

    # CHANGED: Re-detect face on final frame and use the exact selected face
    faces_raw = detect_faces(live_frame)
    face_box, err = pick_single_face(faces_raw, live_frame)
    if err:  # CHANGED
        _release_camera_if_idle(force=True)

        # CHANGED: user-friendly message
        msg = "Face not detected properly."
        if "No face" in str(err):
            msg = "Make sure your face is clearly visible."
        elif "Multiple" in str(err):
            msg = "Only one person should be in the frame."

        return redirect_with_msg("/camera?mode=register", msg)

    x, y, w, h = face_box  # CHANGED

    # CHANGED: Keep face size consistent / reject too-small face
    face_area = w * h  # CHANGED
    frame_area = live_frame.shape[0] * live_frame.shape[1]  # CHANGED
    face_ratio = face_area / frame_area if frame_area > 0 else 0.0  # CHANGED

    if face_ratio < 0.08:  # CHANGED
        state["live_instruction"] = "Move closer"  # CHANGED
        state["live_subtext"] = "Face is too small for a stable capture"  # CHANGED
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/camera?mode=register", "Face is too small for a stable capture. Please move closer.")

    # CHANGED: Reject face that is too close to the camera.
    max_face_ratio = float(globals().get("ENROLLMENT_MAX_FACE_RATIO", 0.45))
    if face_ratio > max_face_ratio:
        state["live_instruction"] = "Move back"
        state["live_subtext"] = "Face is too close for a stable capture"
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/camera?mode=register", "Face is too close. Please move back slightly and try again.")

    # CHANGED: Reject off-centre front registration samples.
    frame_h, frame_w = live_frame.shape[:2]
    center_x = (float(x) + (float(w) / 2.0)) / max(1.0, float(frame_w))
    center_y = (float(y) + (float(h) / 2.0)) / max(1.0, float(frame_h))
    max_center_offset_x = float(globals().get("ENROLLMENT_MAX_CENTER_OFFSET_X", 0.18))
    max_center_offset_y = float(globals().get("ENROLLMENT_MAX_CENTER_OFFSET_Y", 0.22))
    if abs(center_x - 0.5) > max_center_offset_x or abs(center_y - 0.5) > max_center_offset_y:
        state["live_instruction"] = "Centre face"
        state["live_subtext"] = "Face is outside the centre guide"
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/camera?mode=register", "Please centre your face in the guide frame and try again.")

    # CHANGED: Reject extreme angles during registration
    yaw = yaw_ratio_from_face(live_frame, face_box)
    if yaw is not None and abs(yaw) > 0.20:  # CHANGED
        state["live_instruction"] = "Face forward"  # CHANGED
        state["live_subtext"] = "Please look more directly at the camera"  # CHANGED
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/camera?mode=register", "Please look more directly at the camera.")

    # CHANGED: Add padding around the face crop
    pad_x = int(w * 0.15)
    pad_y = int(h * 0.20)

    x1 = max(0, x - pad_x)
    y1 = max(0, y - pad_y)
    x2 = min(live_frame.shape[1], x + w + pad_x)
    y2 = min(live_frame.shape[0], y + h + pad_y)

    face_crop = live_frame[y1:y2, x1:x2]  # CHANGED
    if face_crop is None or face_crop.size == 0:  # CHANGED
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/camera?mode=register", "Failed to crop face correctly.")

    # ============================================================
    # SMART STABILITY CHECK
    # Requires the student to remain still for 2 continuous seconds
    # before embedding capture. If the face moves too much, the timer resets.
    # This balances anti-spoofing with smoother performance on slower connections/devices.
    # ============================================================
    state["live_instruction"] = "Securing identity"
    state["live_subtext"] = "Hold still for 2.0s"

    required_stable_seconds = 2.0
    movement_threshold = 25.0
    max_stability_wait_seconds = 10.0

    stable_start = time.time()
    wait_start = stable_start
    prev_center_x = None
    prev_center_y = None
    last_good_frame = None
    last_good_face_box = None

    while True:
        success, stable_frame = cap.read()

        if not success or stable_frame is None:
            stable_start = time.time()
            state["live_subtext"] = "Camera frame not stable. Hold still..."
            time.sleep(0.08)
            continue

        faces_now = detect_faces(stable_frame)
        stable_face_box, stable_err = pick_single_face(faces_now, stable_frame)

        if stable_err is not None or stable_face_box is None:
            stable_start = time.time()
            prev_center_x = None
            prev_center_y = None
            state["live_subtext"] = "Keep one clear face in frame..."
            time.sleep(0.08)

            if time.time() - wait_start > max_stability_wait_seconds:
                _release_camera_if_idle(force=True)
                return redirect_with_msg(
                    "/camera?mode=register",
                    "Unable to confirm stable face. Please keep one face visible and try again."
                )
            continue

        x_now, y_now, w_now, h_now = stable_face_box
        center_x = x_now + (w_now / 2.0)
        center_y = y_now + (h_now / 2.0)

        if prev_center_x is not None and prev_center_y is not None:
            movement = abs(center_x - prev_center_x) + abs(center_y - prev_center_y)

            # CHANGED: reset the 2-second timer if the student moves too much.
            if movement > movement_threshold:
                stable_start = time.time()
                state["live_subtext"] = "Movement detected. Hold still again..."

        prev_center_x = center_x
        prev_center_y = center_y
        last_good_frame = stable_frame
        last_good_face_box = stable_face_box

        stable_elapsed = time.time() - stable_start
        remaining = max(0.0, required_stable_seconds - stable_elapsed)
        state["live_subtext"] = f"Hold still... {remaining:.1f}s"

        if stable_elapsed >= required_stable_seconds:
            break

        if time.time() - wait_start > max_stability_wait_seconds:
            _release_camera_if_idle(force=True)
            return redirect_with_msg(
                "/camera?mode=register",
                "Too much movement detected. Please hold still and try again."
            )

        time.sleep(0.08)

    # CHANGED: Use the final stable frame for embedding generation, not the
    # motion frame used during blink/head-turn liveness.
    if last_good_frame is not None and last_good_face_box is not None:
        live_frame = last_good_frame
        face_box = last_good_face_box
        x, y, w, h = face_box

        pad_x = int(w * 0.15)
        pad_y = int(h * 0.20)

        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(live_frame.shape[1], x + w + pad_x)
        y2 = min(live_frame.shape[0], y + h + pad_y)

        face_crop = live_frame[y1:y2, x1:x2]

        if face_crop is None or face_crop.size == 0:
            _release_camera_if_idle(force=True)
            return redirect_with_msg(
                "/camera?mode=register",
                "Failed to crop stable face correctly. Please try again."
            )

    # CHANGED: Reject blurry frames
    gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
    sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
    if sharpness < 80:  # CHANGED
        state["live_instruction"] = "Hold still"  # CHANGED
        state["live_subtext"] = "Image is blurry"  # CHANGED
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/camera?mode=register", "Image is blurry. Please hold still and try again.")

    # CHANGED: Keep lighting reasonable
    brightness = float(np.mean(gray))
    if brightness < 50:  # CHANGED
        state["live_instruction"] = "Improve lighting"  # CHANGED
        state["live_subtext"] = "Face is too dark"  # CHANGED
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/camera?mode=register", "Face is too dark. Please improve lighting and try again.")

    # CHANGED: Reject overexposed or low-contrast stable frames too.
    contrast = float(np.std(gray))
    max_brightness = float(globals().get("ENROLLMENT_MAX_BRIGHTNESS", 215.0))
    min_contrast = float(globals().get("ENROLLMENT_MIN_CONTRAST", 18.0))

    if brightness > max_brightness:
        state["live_instruction"] = "Reduce lighting"
        state["live_subtext"] = "Face is too bright"
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/camera?mode=register", "Face is too bright. Please reduce lighting and try again.")

    if contrast < min_contrast:
        state["live_instruction"] = "Improve lighting"
        state["live_subtext"] = "Face has low contrast"
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/camera?mode=register", "Face has low contrast. Please adjust lighting and try again.")

    cv2.imwrite(os.path.join(RECOG_FOLDER, "recognized.png"), live_frame)

    # CHANGED: Generate embedding from cropped face instead of whole frame
    emb, err = generate_embedding(face_crop)
    if err:
        # CHANGED: Do not wipe ts_key — previously captured samples must survive a bad frame
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/camera?mode=register", "Failed to generate embedding. Please try again.")

    emb_list = np.asarray(emb, dtype=np.float32).reshape(-1).tolist()
    if len(emb_list) != 128:
        # CHANGED: Same — preserve prior samples on invalid embedding
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/camera?mode=register", "Invalid embedding length. Please capture again.")

    consistency_error, consistency_metrics = _registration_identity_consistency_error(ts_key, emb_list)
    if consistency_error:
        print(
            f"[REGISTRATION-CONSISTENCY] rejected reason={consistency_error} "
            f"metrics={consistency_metrics}",
            flush=True,
        )
        should_reset, fail_count, max_soft_fails = _registration_note_consistency_failure(
            ts_key,
            consistency_metrics,
        )
        if should_reset:
            _registration_clear_pending_samples(ts_key)
            _release_camera_if_idle(force=True)
            return redirect_with_msg("/camera?mode=register", consistency_error)

        retry_message = _registration_consistency_recapture_message(
            consistency_metrics,
            fail_count=fail_count,
            max_soft_fails=max_soft_fails,
        )
        state["live_instruction"] = "Recapture needed"
        state["live_subtext"] = "Keep the same person and face straight"
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/camera?mode=register", retry_message)

    # CHANGED: Append this embedding to the pending store list
    _pending_store_put(ts_key, emb_list)
    _registration_clear_consistency_failures(ts_key)

    captures_done = _pending_store_get_count(ts_key)

    # CHANGED:
    # Try to collect monitoring-support embeddings after every successful
    # capture. Front samples remain required; screen/side samples are optional
    # continuous-monitoring support only.
    _store_monitor_screen_front_support_embeddings(ts_key, state)
    _store_monitor_side_support_embeddings(ts_key, state)
    print(f"   ✅ Capture {captures_done}/{REGISTRATION_SAMPLE_COUNT} done for ts_key={ts_key}", flush=True)

    # CHANGED: unified success wording
    if captures_done < REGISTRATION_SAMPLE_COUNT:
        state["live_instruction"] = "Face capture successful"  # CHANGED
        state["live_subtext"] = f"Progress: {captures_done}/{REGISTRATION_SAMPLE_COUNT}"  # CHANGED
    else:
        state["live_instruction"] = "Registration samples complete"  # CHANGED
        state["live_subtext"] = f"Progress: {captures_done}/{REGISTRATION_SAMPLE_COUNT}"  # CHANGED

    _release_camera_if_idle(force=True)
    return redirect("/register?keep=1")

@app.route("/api/liveness/validate-phase", methods=["POST"])
def api_liveness_validate_phase():
    if not _require_csrf_json():
        return fail("CSRF failed", 400)

    data = request.get_json(silent=True) or {}
    phase = str(data.get("phase") or "").strip().lower()
    sequence_payload = (
        data.get("liveness_sequence")
        or data.get("sequence")
        or data.get("frames")
    )

    print(
        f"[LIVENESS-PHASE-API] received phase={phase or '(missing)'}, "
        f"payload_type={type(sequence_payload).__name__}",
        flush=True,
    )

    if isinstance(sequence_payload, (dict, list)):
        sequence_data = json.dumps(sequence_payload)
    elif isinstance(sequence_payload, str):
        sequence_data = sequence_payload
    else:
        print(
            f"[LIVENESS-PHASE-API] phase={phase or '(missing)'} failed: "
            "missing live camera frames",
            flush=True,
        )
        return fail("Missing live camera frames. Please try again.", 400)

    # CHANGED:
    # ``screen_front`` is a registration-only monitoring-support phase.
    # Validate it using the existing front-facing backend validation path, then
    # store its decoded frames separately for monitoring support.
    validation_phase = "front" if phase == "screen_front" else phase

    ok_phase, phase_data, err = validate_browser_liveness_phase(
        sequence_data,
        validation_phase,
        _get_stream_key(),
    )
    if not ok_phase:
        print(
            f"[LIVENESS-PHASE-API] phase={phase or '(missing)'} failed: "
            f"{err or 'Liveness step failed'}",
            flush=True,
        )
        return fail(err or "Liveness step failed. Please try again.", 400)

    try:
        phase_debug = str(phase_data or {"phase": phase})
        if len(phase_debug) > 500:
            phase_debug = phase_debug[:500] + "...(truncated)"
    except Exception:
        phase_debug = "<unprintable>"

    print(
        f"[LIVENESS-PHASE-API] phase={phase or '(missing)'} passed: {phase_debug}",
        flush=True,
    )

    if phase == "screen_front":
        try:
            state = _ensure_liveness_state(_get_stream_key())
            screen_frames = _extract_liveness_phase_frames(
                sequence_payload,
                phase_names=("screen_front",),
                max_frames=18,
            )
            if screen_frames:
                state["screen_monitor_frames"] = screen_frames
                print(
                    f"[SCREEN-FRONT-SUPPORT] collected_frames={len(screen_frames)} purpose=monitoring_only",
                    flush=True,
                )
            else:
                print(
                    "[SCREEN-FRONT-SUPPORT] no decoded frames collected",
                    flush=True,
                )
        except Exception as screen_err:
            print(
                f"[SCREEN-FRONT-SUPPORT] collect_failed={type(screen_err).__name__}",
                flush=True,
            )

    # CHANGED:
    # Store decoded left/right phase frames for automatic monitoring-support
    # collection. These are monitoring-only references and are not saved to the
    # strict front identity list used by quiz verification / re-verification.
    if phase in ("move_left", "move_right"):
        try:
            state = _ensure_liveness_state(_get_stream_key())
            pose = "left" if phase == "move_left" else "right"
            pose_frames = _extract_liveness_phase_frames(
                sequence_payload,
                phase_names=(phase,),
                max_frames=18,
            )
            if pose_frames:
                state[f"{pose}_monitor_frames"] = pose_frames
                print(
                    f"[POSE-SUPPORT-EMBEDDING] pose={pose} collected_frames={len(pose_frames)} purpose=monitoring_only",
                    flush=True,
                )
            else:
                print(
                    f"[POSE-SUPPORT-EMBEDDING] pose={pose} no decoded frames collected",
                    flush=True,
                )
        except Exception as pose_collect_err:
            print(
                f"[POSE-SUPPORT-EMBEDDING] collect_failed phase={phase} error={type(pose_collect_err).__name__}",
                flush=True,
            )

    response_phase_data = phase_data or {"phase": validation_phase}
    if isinstance(response_phase_data, dict):
        response_phase_data = dict(response_phase_data)
        response_phase_data["phase"] = phase or validation_phase
        if phase == "screen_front":
            response_phase_data["purpose"] = "continuous_monitoring_support_only"

    return ok(response_phase_data, f"{phase.replace('_', ' ').title()} validated")

@app.route("/api/capture/status", methods=["GET"])
def api_capture_status():
    """
    CHANGED: Returns how many face samples have been collected so far for the
    current registration session. The frontend uses this to show progress and
    to block the Register button until all required samples are captured.
    """
    ts_key = session.get("ts", "")
    captures_done = _pending_store_get_count(ts_key) if ts_key else 0
    ready = captures_done >= REGISTRATION_SAMPLE_COUNT
    return ok(
        {
            "captures_done": captures_done,
            "captures_required": REGISTRATION_SAMPLE_COUNT,
            "ready": ready,
        },
        "Capture status"
    )

@app.route("/api/camera-permission-required", methods=["GET"])
def api_camera_permission_required():
    """
    CHANGED: Signals to the quiz session frontend that camera permission MUST be
    requested via navigator.mediaDevices.getUserMedia() before quiz monitoring
    may begin.

    The frontend JS in stud-quiz-session.html must follow this exact flow on
    DOMContentLoaded

        1. Call GET /api/camera-permission-required to confirm the requirement.
        2. Call navigator.mediaDevices.getUserMedia({ video: true }).
        3. On RESOLVE  → store the MediaStream, mark camera as granted, then
                         start the monitoring interval (send frames to /api/face/embed
                         and results to /api/quiz-attempts/<id>/face-check).
        4. On REJECT   → show camera_denied_message (returned in this response),
                         disable all quiz interaction (grey out / overlay),
                         and do NOT start monitoring. The quiz cannot proceed
                         until the student grants permission and reloads.

    This endpoint itself only returns the configuration; the actual permission
    prompt is triggered entirely in the browser by getUserMedia().
    """
    message = (
        "Camera access is required to take this quiz. "
        "Please allow camera access in your browser, then reload the page."
    )
    return ok(
        {
            "camera_required": True,
            "message": message,
        },
        "Camera permission required"
    )
