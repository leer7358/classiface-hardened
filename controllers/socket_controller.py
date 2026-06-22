"""Socket.IO event handlers for quiz monitoring."""

from ._shared import _set_liveness_running, load_app_context


load_app_context(globals())


def _ws_reverify_quality_error_from_metrics(payload):
    """
    CHANGED:
    Backend safeguard for WebSocket re-verify.

    The frontend now sends quality_metrics for re-verify frames.
    If the frame is obviously weak, ask the student to retry instead of
    counting it as a face mismatch.

    This does NOT change the distance formula, 85% threshold, or majority rule.
    """
    payload = payload or {}
    metrics = payload.get("quality_metrics") or payload.get("qualityMetrics") or {}

    if not isinstance(metrics, dict) or not metrics:
        return None, {}

    try:
        brightness = float(metrics.get("brightness") or 0)
        contrast = float(metrics.get("contrast") or 0)
        focus = float(metrics.get("focus") or 0)

        face_ratio = metrics.get("face_ratio")
        if face_ratio is None:
            face_ratio = metrics.get("faceRatio")
        face_ratio = None if face_ratio is None else float(face_ratio)

        center_offset_x = metrics.get("center_offset_x")
        if center_offset_x is None:
            center_offset_x = metrics.get("centerOffsetX")
        center_offset_x = None if center_offset_x is None else float(center_offset_x)

        center_offset_y = metrics.get("center_offset_y")
        if center_offset_y is None:
            center_offset_y = metrics.get("centerOffsetY")
        center_offset_y = None if center_offset_y is None else float(center_offset_y)

        yaw_ratio = payload.get("yaw_ratio")
        if yaw_ratio is None:
            yaw_ratio = payload.get("yawRatio")
        yaw_ratio = None if yaw_ratio is None else float(yaw_ratio)

        # CHANGED:
        # Re-verify may happen under slightly dimmer quiz-session lighting.
        # Only reject extremely dark frames here; otherwise let identity matching decide.
        min_brightness = float(globals().get("WS_REVERIFY_MIN_BRIGHTNESS", 30.0))
        max_brightness = float(globals().get("ENROLLMENT_MAX_BRIGHTNESS", 215.0))
        min_contrast = float(globals().get("WS_REVERIFY_MIN_CONTRAST", 10.0))
        min_face_area = float(globals().get("ENROLLMENT_MIN_FACE_AREA", 0.045))
        max_face_area = float(globals().get("ENROLLMENT_MAX_FACE_AREA", 0.65))

        safe_metrics = {
            "brightness": round(brightness, 2),
            "contrast": round(contrast, 2),
            "focus": round(focus, 2),
            "face_ratio": None if face_ratio is None else round(face_ratio, 4),
            "center_offset_x": None if center_offset_x is None else round(center_offset_x, 4),
            "center_offset_y": None if center_offset_y is None else round(center_offset_y, 4),
            "yaw_ratio": None if yaw_ratio is None else round(yaw_ratio, 4),
        }

        if brightness < min_brightness:
            return "Face is extremely dark. Please improve lighting and try again.", safe_metrics
        if brightness > max_brightness:
            return "Face is too bright. Please reduce lighting and try again.", safe_metrics
        if contrast < min_contrast:
            return "Face has low contrast. Please adjust lighting and try again.", safe_metrics
        # CHANGED:
        # The browser sends low-resolution hidden-monitor frames for re-verify.
        # Treat only extremely low focus as a quality retry.
        if focus < 0.5:
            return "Image is very blurry. Please hold still and try again.", safe_metrics
        if face_ratio is not None and face_ratio < min_face_area:
            return "Face is too small. Please move closer and try again.", safe_metrics
        if face_ratio is not None and face_ratio > max_face_area:
            return "Face is too close. Please move back slightly and try again.", safe_metrics
        if center_offset_x is not None and center_offset_y is not None:
            # CHANGED:
            # Align accepted centre area for re-verify with quiz verification.
            # This keeps re-verify and quiz entry using the same centre tolerance.
            if center_offset_x > 0.20 or center_offset_y > 0.20:
                return "Please centre your face and try again.", safe_metrics
        if yaw_ratio is not None and abs(yaw_ratio) > 0.14:
            return "Please look straight at the camera and try again.", safe_metrics

        return None, safe_metrics

    except Exception as err:
        print(f"⚠️ WS reverify metric quality check skipped: {type(err).__name__}", flush=True)
        return None, {}


def _ws_reverify_quality_error_from_frame(frame, face_box):
    """
    CHANGED:
    Server-side quality check for decoded WS re-verify frames.
    This protects the backend even if the browser quality metrics are missing.
    """
    if frame is None or face_box is None:
        return "No clear face detected. Please try again.", {}

    try:
        x, y, w, h = face_box
        frame_h, frame_w = frame.shape[:2]
        frame_area = max(1, frame_w * frame_h)
        face_ratio = float((w * h) / frame_area)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(np.mean(gray))
        contrast = float(np.std(gray))

        face_center_x = float((x + (w / 2.0)) / max(1, frame_w))
        face_center_y = float((y + (h / 2.0)) / max(1, frame_h))
        center_offset_x = abs(face_center_x - 0.5)
        center_offset_y = abs(face_center_y - 0.5)

        # CHANGED:
        # Re-verify may use a higher-quality browser frame, but still comes
        # from the quiz-session camera. Keep this as a safety guard only and
        # avoid making blur stricter than the actual 85% identity match.
        min_blur = float(globals().get("WS_REVERIFY_MIN_BLUR_SCORE", 15.0))
        # CHANGED:
        # Re-verify may happen under slightly dimmer quiz-session lighting.
        # Only reject extremely dark frames here; otherwise let identity matching decide.
        min_brightness = float(globals().get("WS_REVERIFY_MIN_BRIGHTNESS", 30.0))
        max_brightness = float(globals().get("ENROLLMENT_MAX_BRIGHTNESS", 215.0))
        min_contrast = float(globals().get("WS_REVERIFY_MIN_CONTRAST", 10.0))
        min_face_area = float(globals().get("ENROLLMENT_MIN_FACE_AREA", 0.045))
        max_face_area = float(globals().get("ENROLLMENT_MAX_FACE_AREA", 0.65))
        center_tolerance = float(globals().get("WS_REVERIFY_CENTER_TOLERANCE", 0.20))

        metrics = {
            "blur": round(blur, 2),
            "brightness": round(brightness, 2),
            "contrast": round(contrast, 2),
            "face_ratio": round(face_ratio, 4),
            "center_offset_x": round(center_offset_x, 4),
            "center_offset_y": round(center_offset_y, 4),
        }

        if blur < min_blur:
            return "Image is very blurry. Please hold still and try again.", metrics
        if brightness < min_brightness:
            return "Face is extremely dark. Please improve lighting and try again.", metrics
        if brightness > max_brightness:
            return "Face is too bright. Please reduce lighting and try again.", metrics
        if contrast < min_contrast:
            return "Face has low contrast. Please adjust lighting and try again.", metrics
        if face_ratio < min_face_area:
            return "Face is too small. Please move closer and try again.", metrics
        if face_ratio > max_face_area:
            return "Face is too close. Please move back slightly and try again.", metrics
        if center_offset_x > center_tolerance or center_offset_y > center_tolerance:
            return "Please centre your face and try again.", metrics

        return None, metrics

    except Exception as err:
        print(f"⚠️ WS reverify frame quality check skipped: {type(err).__name__}", flush=True)
        return None, {}


def _ws_monitoring_poor_quality_from_metrics(payload):
    """
    CHANGED:
    Continuous monitoring quality tolerance.

    If a normal monitoring frame is obviously poor quality, do not treat the
    resulting identity failure as a face mismatch. The next frame will be checked
    again. This does not apply to quiz entry or re-verify.
    """
    payload = payload or {}
    metrics = payload.get("quality_metrics") or payload.get("qualityMetrics") or {}

    if not isinstance(metrics, dict) or not metrics:
        return None, {}

    try:
        brightness = float(metrics.get("brightness") or 0)
        contrast = float(metrics.get("contrast") or 0)
        focus = float(metrics.get("focus") or 0)

        face_ratio = metrics.get("face_ratio")
        if face_ratio is None:
            face_ratio = metrics.get("faceRatio")
        face_ratio = None if face_ratio is None else float(face_ratio)

        center_offset_x = metrics.get("center_offset_x")
        if center_offset_x is None:
            center_offset_x = metrics.get("centerOffsetX")
        center_offset_x = None if center_offset_x is None else float(center_offset_x)

        center_offset_y = metrics.get("center_offset_y")
        if center_offset_y is None:
            center_offset_y = metrics.get("centerOffsetY")
        center_offset_y = None if center_offset_y is None else float(center_offset_y)

        safe_metrics = {
            "brightness": round(brightness, 2),
            "contrast": round(contrast, 2),
            "focus": round(focus, 2),
            "face_ratio": None if face_ratio is None else round(face_ratio, 4),
            "center_offset_x": None if center_offset_x is None else round(center_offset_x, 4),
            "center_offset_y": None if center_offset_y is None else round(center_offset_y, 4),
        }

        if brightness < 25:
            return "monitoring_frame_too_dark", safe_metrics
        if brightness > 240:
            return "monitoring_frame_too_bright", safe_metrics
        if contrast < 8:
            return "monitoring_frame_low_contrast", safe_metrics
        if focus < 1.5:
            return "monitoring_frame_blurry", safe_metrics
        if face_ratio is not None and face_ratio < 0.035:
            return "monitoring_face_too_small", safe_metrics
        if face_ratio is not None and face_ratio > 0.72:
            return "monitoring_face_too_close", safe_metrics
        if center_offset_x is not None and center_offset_y is not None:
            if center_offset_x > 0.28 or center_offset_y > 0.28:
                return "monitoring_face_off_center", safe_metrics

        return None, safe_metrics

    except Exception as err:
        print(f"⚠️ WS monitoring metric quality check skipped: {type(err).__name__}", flush=True)
        return None, {}


def _emit_reverify_quality_retry(message, metrics=None):
    """
    CHANGED:
    Tell the frontend to retry re-verify capture without counting a mismatch.
    """
    emit("face_check_result", {
        "ok": True,
        "status": "monitoring_tolerated",
        "reason": "reverify_quality_retry",
        "verification_mode": "reverify",
        "confidence": None,
        "confidence_percent": None,
        "face_count": 1,
        "action": "retry_capture",
        "message": message or "Please capture a clearer verification frame.",
        "quality_metrics": metrics or {},
    })


def _ws_pose_from_yaw(yaw_ratio):
    """
    CHANGED:
    Classify current monitoring pose using backend yaw where available.

    Returns:
      - front
      - left
      - right

    Re-verify and quiz entry do not use this; they stay front-only.
    """
    try:
        yaw_value = float(yaw_ratio)
    except Exception:
        return "front"

    side_min = float(globals().get("WS_MONITOR_SIDE_POSE_YAW_MIN", 0.055))
    if abs(yaw_value) < side_min:
        return "front"

    sign = 1 if yaw_value > 0 else -1
    left_sign = int(globals().get("LIVENESS_LEFT_YAW_SIGN", 1))
    return "left" if sign == left_sign else "right"


def _ws_get_pose_embedding_bank(firebase_uid, pose):
    """
    CHANGED:
    Load pose-specific monitoring embeddings when available.
    """
    pose = str(pose or "front").strip().lower()
    if pose not in ("front", "left", "right"):
        pose = "front"

    loader = globals().get("fb_get_decrypted_pose_monitor_embeddings_cached")
    if callable(loader):
        try:
            pose_embs = loader(firebase_uid, pose) or []
            if pose_embs:
                return pose_embs, f"{pose}_monitor_embeddings"
        except Exception as pose_err:
            print(
                f"⚠️ WS pose embedding load failed pose={pose}: {type(pose_err).__name__}",
                flush=True,
            )

    if pose == "front":
        return fb_get_decrypted_embeddings_cached(firebase_uid) or [], "registered_embeddings"

    return [], f"{pose}_monitor_embeddings"



@socketio.on("connect")
def handle_connect():  # CHANGED
    print(f"✅ Socket client connected: {request.sid}", flush=True)
    emit("connected", {"ok": True, "sid": request.sid})

@socketio.on("disconnect")
def handle_disconnect():  # CHANGED
    print(f"❌ Socket client disconnected: {request.sid}", flush=True)

@socketio.on("join_room")
def handle_join_room(data):  # CHANGED
    room = str((data or {}).get("room") or "").strip()
    if not room:  # CHANGED
        emit("error", {"message": "room is required"})
        return  # CHANGED

    join_room(room)
    print(f"👤 Joined room: {room}", flush=True)
    emit("joined_room", {"room": room})

@socketio.on("leave_room")
def handle_leave_room(data):  # CHANGED
    room = str((data or {}).get("room") or "").strip()
    if not room:  # CHANGED
        emit("error", {"message": "room is required"})
        return  # CHANGED

    leave_room(room)
    print(f"👋 Left room: {room}", flush=True)
    emit("left_room", {"room": room})

@socketio.on("monitor_event")
def handle_monitor_event(data):  # CHANGED
    """
    Student page can emit monitoring updates here.
    This does NOT replace your REST logging; it complements it.
    """  # CHANGED
    payload = data or {}  # CHANGED
    attempt_id = str(payload.get("attempt_id") or "").strip()
    class_id = str(payload.get("class_id") or "").strip()
    quiz_id = str(payload.get("quiz_id") or "").strip()
    event_type = str(payload.get("event_type") or "").strip()

    if not attempt_id:  # CHANGED
        emit("error", {"message": "attempt_id is required"})
        return  # CHANGED

    if event_type == "warning":  # CHANGED
        _emit_student_warning(attempt_id, payload)

    if event_type == "blackout_on":  # CHANGED
        _emit_student_blackout_on(attempt_id, payload)
        if class_id and quiz_id:  # CHANGED
            _emit_instructor_violation_alert(class_id, quiz_id, payload)

    if event_type == "blackout_off":  # CHANGED
        _emit_student_blackout_off(attempt_id, payload)

    emit("monitor_ack", {"ok": True, "event_type": event_type})

def _decode_ws_frame_embedding_candidate(item, verification_mode=""):
    """
    CHANGED:
    Decode one browser frame candidate and generate an embedding.

    This is used for both continuous monitoring and re-verify. The matching
    threshold and majority logic still run later in handle_face_check_embedding().
    """
    try:
        import base64

        payload = item if isinstance(item, dict) else {"frame": item}
        frame_b64 = payload.get("frame") or payload.get("image")

        if not frame_b64:
            return {
                "ok": False,
                "status": "no_face",
                "message": "No frame received",
                "source": payload.get("source") or "candidate",
            }

        if "," in frame_b64:
            frame_b64 = frame_b64.split(",", 1)[1]

        frame_bytes = base64.b64decode(frame_b64)
        file_bytes = np.frombuffer(frame_bytes, dtype=np.uint8)
        frame = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

        if frame is None:
            return {
                "ok": False,
                "status": "no_face",
                "message": "Could not decode image",
                "source": payload.get("source") or "candidate",
            }

        faces_raw = detect_faces(frame)
        filtered = filter_faces(faces_raw, frame.shape)
        face_count = len(filtered)

        if face_count == 0:
            return {
                "ok": False,
                "status": "no_face",
                "message": "No face detected",
                "face_count": 0,
                "source": payload.get("source") or "candidate",
            }

        if face_count > 1:
            return {
                "ok": False,
                "status": "multiple_faces",
                "message": "Multiple faces detected",
                "face_count": face_count,
                "source": payload.get("source") or "candidate",
            }

        face_box, err = pick_single_face(filtered, frame)
        if err or face_box is None:
            return {
                "ok": False,
                "status": "no_face",
                "message": err or "No usable face detected",
                "face_count": face_count,
                "source": payload.get("source") or "candidate",
            }

        # CHANGED:
        # Compute backend yaw for pose-aware continuous monitoring.
        # This is more reliable than the browser's simple face-centre estimate.
        try:
            backend_yaw_ratio = yaw_ratio_from_face(frame, face_box)
        except Exception:
            backend_yaw_ratio = payload.get("yaw_ratio") or payload.get("yawRatio")

        x, y, w, h = face_box
        pad_x = int(w * 0.20)
        pad_y = int(h * 0.20)

        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(frame.shape[1], x + w + pad_x)
        y2 = min(frame.shape[0], y + h + pad_y)

        face_crop = frame[y1:y2, x1:x2]

        if face_crop is None or face_crop.size == 0:
            return {
                "ok": False,
                "status": "no_face",
                "message": "Invalid face crop",
                "face_count": face_count,
                "source": payload.get("source") or "candidate",
            }

        if face_crop.shape[0] < 40 or face_crop.shape[1] < 40:
            return {
                "ok": False,
                "status": "no_face",
                "message": "Face too small",
                "face_count": face_count,
                "source": payload.get("source") or "candidate",
            }

        mode = str(verification_mode or "").strip().lower()
        is_reverify_frame = mode in (
            "reverify",
            "re_verify",
            "re-verification",
            "reverification",
            "identity_reverify",
            "identity-reverify",
        )

        quality_metrics = payload.get("quality_metrics") or payload.get("qualityMetrics") or {}

        if is_reverify_frame:
            quality_error, frame_quality_metrics = _ws_reverify_quality_error_from_frame(frame, face_box)
            print(
                f"[WS-REVERIFY-QUALITY] candidate={payload.get('source') or 'candidate'} "
                f"frame metrics={frame_quality_metrics} accepted={quality_error is None}",
                flush=True,
            )
            if quality_error:
                return {
                    "ok": False,
                    "status": "retry_capture",
                    "message": quality_error,
                    "quality_metrics": frame_quality_metrics,
                    "face_count": face_count,
                    "source": payload.get("source") or "candidate",
                }

            if frame_quality_metrics:
                quality_metrics = frame_quality_metrics

        emb, err = generate_embedding(face_crop)
        if err:
            return {
                "ok": False,
                "status": "no_face",
                "message": err,
                "face_count": face_count,
                "source": payload.get("source") or "candidate",
            }

        emb_list = np.asarray(emb, dtype=np.float32).reshape(-1).tolist()
        if len(emb_list) != 128:
            return {
                "ok": False,
                "status": "no_face",
                "message": "Embedding error",
                "face_count": face_count,
                "source": payload.get("source") or "candidate",
            }

        return {
            "ok": True,
            "status": "ok",
            "embedding": emb_list,
            "face_count": face_count,
            "quality_metrics": quality_metrics,
            "yaw_ratio": backend_yaw_ratio,
            "source": payload.get("source") or "candidate",
        }

    except Exception as err:
        return {
            "ok": False,
            "status": "error",
            "message": str(err),
            "source": "candidate",
        }


@socketio.on("student_monitor_frame")
def handle_student_monitor_frame(data):  # CHANGED
    try:
        import base64  # CHANGED

        payload = data or {}
        verification_mode = str(
            payload.get("verification_mode")
            or payload.get("verificationMode")
            or payload.get("mode")
            or ""
        ).strip().lower()

        frame_candidates = payload.get("frame_candidates") or payload.get("frameCandidates") or []
        if isinstance(frame_candidates, list) and frame_candidates:
            decoded_candidates = []
            fallback_error = None
            has_multiple_faces = False

            for index, item in enumerate(frame_candidates[:5], start=1):
                if isinstance(item, dict) and not item.get("source"):
                    item = dict(item)
                    item["source"] = f"candidate_{index}"

                result = _decode_ws_frame_embedding_candidate(item, verification_mode)
                if result.get("ok"):
                    decoded_candidates.append({
                        "embedding": result.get("embedding"),
                        "source": result.get("source") or f"candidate_{index}",
                        "quality_metrics": result.get("quality_metrics") or {},
                        "yaw_ratio": result.get("yaw_ratio"),
                    })
                else:
                    fallback_error = fallback_error or result
                    if result.get("status") == "multiple_faces":
                        has_multiple_faces = True

            if decoded_candidates:
                payload["embedding_candidates"] = decoded_candidates
                payload["embedding"] = decoded_candidates[0]["embedding"]
                payload["face_count"] = 1
                payload["candidate_count"] = len(decoded_candidates)
                payload["quality_metrics"] = decoded_candidates[0].get("quality_metrics") or payload.get("quality_metrics")
                print(
                    f"✅ WS decoded {len(decoded_candidates)} frame candidate(s) for "
                    f"{'re-verify' if verification_mode.startswith('re') else 'monitor'}",
                    flush=True,
                )
                return handle_face_check_embedding(payload)

            if has_multiple_faces:
                emit("face_check_result", {
                    "ok": True,
                    "status": "multiple_faces",
                    "confidence": 0.0,
                    "confidence_percent": 0.0,
                    "face_count": 2,
                    "message": "Multiple faces detected"
                })
                return

            if fallback_error and fallback_error.get("status") == "retry_capture":
                _emit_reverify_quality_retry(
                    fallback_error.get("message") or "Please capture a clearer frame.",
                    fallback_error.get("quality_metrics") or {},
                )
                return

            emit("face_check_result", {
                "ok": True,
                "status": "no_face",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": 0,
                "message": (fallback_error or {}).get("message") or "No usable frame candidate"
            })
            return

        frame_b64 = (data or {}).get("frame")

        if not frame_b64:
            emit("face_check_result", {
                "ok": True,
                "status": "no_face",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": 0,
                "message": "No frame received"
            })
            return

        if "," in frame_b64:
            frame_b64 = frame_b64.split(",", 1)[1]

        frame_bytes = base64.b64decode(frame_b64)
        file_bytes = np.frombuffer(frame_bytes, dtype=np.uint8)
        frame = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

        if frame is None:
            emit("face_check_result", {
                "ok": True,
                "status": "no_face",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": 0,
                "message": "Could not decode image"
            })
            return

        # SAME AS /api/face/embed
        faces_raw = detect_faces(frame)
        filtered = filter_faces(faces_raw, frame.shape)
        face_count = len(filtered)

        if face_count == 0:
            emit("face_check_result", {
                "ok": True,
                "status": "no_face",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": 0,
                "message": "No face detected"
            })
            return

        if face_count > 1:
            emit("face_check_result", {
                "ok": True,
                "status": "multiple_faces",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
                "message": "Multiple faces detected"
            })
            return

        # SAME AS /api/face/embed
        face_box, err = pick_single_face(filtered, frame)

        if err or face_box is None:
            emit("face_check_result", {
                "ok": True,
                "status": "no_face",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
                "message": err or "No usable face detected"
            })
            return

        x, y, w, h = face_box

        # SAME CROP STYLE AS YOUR VERIFY FLOW: 20% padding
        pad_x = int(w * 0.20)
        pad_y = int(h * 0.20)

        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(frame.shape[1], x + w + pad_x)
        y2 = min(frame.shape[0], y + h + pad_y)

        face_crop = frame[y1:y2, x1:x2]

        if face_crop is None or face_crop.size == 0:
            emit("face_check_result", {
                "ok": True,
                "status": "no_face",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
                "message": "Invalid face crop"
            })
            return

        if face_crop.shape[0] < 40 or face_crop.shape[1] < 40:
            emit("face_check_result", {
                "ok": True,
                "status": "no_face",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
                "message": "Face too small"
            })
            return

        # CHANGED:
        # For WS re-verify, do not generate/compare an embedding from a weak frame.
        # Ask the browser to retry capture instead of counting a face mismatch.
        verification_mode = str(
            (data or {}).get("verification_mode")
            or (data or {}).get("verificationMode")
            or (data or {}).get("mode")
            or ""
        ).strip().lower()

        is_reverify_frame = verification_mode in (
            "reverify",
            "re_verify",
            "re-verification",
            "reverification",
            "identity_reverify",
            "identity-reverify",
        )

        if is_reverify_frame:
            quality_error, quality_metrics = _ws_reverify_quality_error_from_frame(frame, face_box)
            print(
                f"[WS-REVERIFY-QUALITY] frame metrics={quality_metrics} "
                f"accepted={quality_error is None}",
                flush=True,
            )
            if quality_error:
                _emit_reverify_quality_retry(quality_error, quality_metrics)
                return

        # SAME EMBEDDING CALL STYLE
        emb, err = generate_embedding(face_crop)

        if err:
            emit("face_check_result", {
                "ok": True,
                "status": "no_face",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
                "message": err
            })
            return

        emb_list = np.asarray(emb, dtype=np.float32).reshape(-1).tolist()

        if len(emb_list) != 128:
            emit("face_check_result", {
                "ok": True,
                "status": "no_face",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
                "message": "Embedding error"
            })
            return

        data["embedding"] = emb_list
        data["face_count"] = face_count

        # IMPORTANT: original confidence/distance/threshold logic stays here
        return handle_face_check_embedding(data)

    except Exception as e:
        print(f"❌ student_monitor_frame error: {str(e)}", flush=True)
        emit("face_check_result", {
            "ok": False,
            "status": "error",
            "message": str(e)
        })

@socketio.on("face_check_request")
def handle_face_check_request(data):  # CHANGED
    """
    CHANGED:
    Alias for face_check_embedding so the frontend can use either event name.
    Keeps the SAME face recognition logic unchanged.
    """
    return handle_face_check_embedding(data)

@socketio.on("face_check_embedding")
def handle_face_check_embedding(data):  # CHANGED
    """
    CHANGED:
    WebSocket-based periodic face monitoring.
    This keeps the SAME facial recognition logic as /api/quiz-attempts/<attempt_id>/face-check
    but receives the embedding via Socket.IO instead of REST.

    IMPORTANT:
    - Face verification / re-verify uses the 5 frontal registered embeddings.
    - Continuous monitoring uses 7 monitoring-support embeddings when available.
    - Both paths use the same majority distance calculation.
    - Re-verify uses 4/5 frontal agreement.
    - Continuous monitoring uses 5/7 when side-support embeddings are available.
    - Continuous monitoring is less strict only through support embeddings, grace counters,
      temporary no-face tolerance, and motion tolerance.
    - blackout_off is only emitted if the attempt was previously paused.
    """
    try:
        guard = student_required()
        if guard:
            emit("face_check_result", {
                "ok": False,
                "status": "error",
                "message": "Not logged in"
            })
            return

        payload = data or {}
        attempt_id = str(payload.get("attempt_id") or "").strip()
        class_id = str(payload.get("class_id") or "").strip()
        quiz_id = str(payload.get("quiz_id") or "").strip()
        embedding = payload.get("embedding")
        face_count = int(payload.get("face_count") or 0)
        time_remaining = payload.get("timeRemaining")

        # CHANGED:
        # Re-verify / identity confirmation must use only the 5 frontal registered embeddings.
        # Normal continuous monitoring can use the 7 monitoring-support embeddings.
        verification_mode = str(
            payload.get("verification_mode")
            or payload.get("verificationMode")
            or payload.get("mode")
            or ""
        ).strip().lower()

        is_reverify = verification_mode in (
            "reverify",
            "re_verify",
            "re-verification",
            "reverification",
            "identity_reverify",
            "identity-reverify",
        )

        if not attempt_id:
            emit("face_check_result", {
                "ok": False,
                "status": "error",
                "message": "attempt_id is required"
            })
            return

        attempt_key = str(attempt_id)

        # CHANGED:
        # If the browser sent re-verify quality metrics and they are clearly bad,
        # do not count this as identity mismatch. Ask frontend to retry capture.
        if is_reverify:
            quality_error, quality_metrics = _ws_reverify_quality_error_from_metrics(payload)
            if quality_metrics:
                print(
                    f"[WS-REVERIFY-QUALITY] payload metrics={quality_metrics} "
                    f"accepted={quality_error is None}",
                    flush=True,
                )
            if quality_error:
                _emit_reverify_quality_retry(quality_error, quality_metrics)
                return

        def _log_violation(vtype: str):
            try:
                with pg_conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO quiz_attempt_violations
                          (id, attempt_id, violation_type, timestamp_iso, time_remaining)
                        VALUES (%s, %s, %s, %s, %s);
                        """,
                        (
                            str(uuid.uuid4()),
                            attempt_key,
                            vtype,
                            datetime.utcnow().isoformat() + "Z",
                            time_remaining,
                        ),
                    )
                    conn.commit()
                    print(f"✅ WS violation logged: {vtype} for attempt {attempt_key}", flush=True)
            except Exception as e:
                print(f"❌ WS violation insert failed [{vtype}]: {str(e)}", flush=True)

        if face_count > 1:
            ATTEMPT_MULTI_FACE_COUNT[attempt_key] = ATTEMPT_MULTI_FACE_COUNT.get(attempt_key, 0) + 1
            ATTEMPT_MISMATCH_COUNT[attempt_key] = 0
            ATTEMPT_NO_FACE_COUNT[attempt_key] = 0

            current_count = ATTEMPT_MULTI_FACE_COUNT[attempt_key]
            print(f"⚠️ WS multiple faces count {current_count}/{MULTI_FACE_GRACE_COUNT} for attempt {attempt_key}", flush=True)

            if current_count < MULTI_FACE_GRACE_COUNT:
                emit("face_check_result", {
                    "ok": True,
                    "status": "multiple_faces",
                    "confidence": 0.0,
                    "confidence_percent": 0.0,
                    "face_count": face_count,
                })
                return

            _log_violation("multiple_faces_detected")
            ATTEMPT_BLACKOUT_STATE[attempt_key] = True
            ATTEMPT_MULTI_FACE_COUNT[attempt_key] = 0

            ws_payload = {
                "attempt_id": attempt_key,
                "class_id": class_id,
                "quiz_id": quiz_id,
                "event_type": "blackout_on",
                "violation_type": "multiple_faces_detected",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "face_count": face_count,
            }

            _emit_student_blackout_on(attempt_key, ws_payload)
            if class_id and quiz_id:
                _emit_instructor_violation_alert(class_id, quiz_id, ws_payload)

            emit("face_check_result", {
                "ok": True,
                "status": "multiple_faces",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
            })
            return

        if embedding is None or embedding == "no_face":
            ATTEMPT_NO_FACE_COUNT[attempt_key] = ATTEMPT_NO_FACE_COUNT.get(attempt_key, 0) + 1
            ATTEMPT_MISMATCH_COUNT[attempt_key] = 0
            ATTEMPT_MULTI_FACE_COUNT[attempt_key] = 0

            current_count = ATTEMPT_NO_FACE_COUNT[attempt_key]
            print(f"⚠️ WS no-face count {current_count}/{NO_FACE_PAUSE_COUNT} for attempt {attempt_key}", flush=True)

            if current_count < NO_FACE_WARNING_COUNT:
                emit("face_check_result", {"ok": True, "status": "no_face", "confidence": 0.0, "confidence_percent": 0.0, "face_count": face_count, "count": current_count, "action": "tolerated"})
                return

            if current_count == NO_FACE_WARNING_COUNT:
                # CHANGED: No warning/logging for temporary no-face because looking down to write is normal.
                emit("face_check_result", {"ok": True, "status": "monitoring_tolerated", "reason": "temporary_no_face", "confidence": 0.0, "confidence_percent": 0.0, "face_count": face_count, "count": current_count, "action": "tolerated"})
                return

            if current_count >= NO_FACE_PAUSE_COUNT:
                _log_violation("no_face_pause")
                ATTEMPT_BLACKOUT_STATE[attempt_key] = True
                ATTEMPT_NO_FACE_COUNT[attempt_key] = 0
                ws_payload = {"attempt_id": attempt_key, "class_id": class_id, "quiz_id": quiz_id, "event_type": "blackout_on", "violation_type": "no_face_pause", "timestamp": datetime.utcnow().isoformat() + "Z", "face_count": face_count}
                print(f"🚨 Emitting student blackout_on no_face_pause (WS): {ws_payload}", flush=True)
                _emit_student_blackout_on(attempt_key, ws_payload)
                if class_id and quiz_id:
                    _emit_instructor_violation_alert(class_id, quiz_id, ws_payload)
                emit("face_check_result", {"ok": True, "status": "no_face", "confidence": 0.0, "confidence_percent": 0.0, "face_count": face_count, "count": current_count, "action": "blackout_on"})
                return

            emit("face_check_result", {"ok": True, "status": "no_face", "confidence": 0.0, "confidence_percent": 0.0, "face_count": face_count, "count": current_count, "action": "waiting"})
            return

        if not isinstance(embedding, list) or len(embedding) != 128:
            emit("face_check_result", {
                "ok": False,
                "status": "error",
                "message": "Invalid embedding format"
            })
            return

        firebase_uid = str(session.get("firebase_uid") or "")
        if not firebase_uid:
            emit("face_check_result", {
                "ok": False,
                "status": "error",
                "message": "Missing Firebase UID in session"
            })
            return

        # CHANGED:
        # Keep the same identity logic, distance calculation, and 85% matching helper
        # for both re-verify and continuous monitoring.
        #
        # Re-verify / identity confirmation:
        #   - 5 frontal registered embeddings only.
        #   - majority identity matching: 4/5 frontal embeddings must agree.
        #
        # Continuous monitoring:
        #   - 7 monitoring-support embeddings when available.
        #   - majority identity matching: 5/7 when monitoring-support embeddings are available.
        #   - if only 5 or 6 monitoring embeddings are available, fallback to 4 required.
        #   - Less strict only because it has more support poses and grace counters.
        stored_embs = []
        embedding_source = "registered_embeddings"
        check_label = "re-verify" if is_reverify else "monitor"

        if is_reverify:
            stored_embs = fb_get_decrypted_embeddings_cached(firebase_uid) or []
        else:
            embedding_source = "monitoring_embeddings"

            try:
                monitor_loader = globals().get("fb_get_decrypted_monitor_embeddings_cached")
                if callable(monitor_loader):
                    stored_embs = monitor_loader(firebase_uid) or []
            except Exception as monitor_err:
                print(
                    f"⚠️ WS monitor embedding load failed: {type(monitor_err).__name__}",
                    flush=True,
                )
                stored_embs = []

            # Fallback only if monitoring-support embeddings are not available.
            if not stored_embs:
                embedding_source = "registered_embeddings"
                stored_embs = fb_get_decrypted_embeddings_cached(firebase_uid) or []

        if stored_embs:
            try:
                print(
                    f"✅ WS using {len(stored_embs)} {embedding_source} for {check_label}",
                    flush=True,
                )
            except Exception:
                pass

        if not stored_embs:
            emit("face_check_result", {
                "ok": True,
                "status": "no_biometrics",
                "verification_mode": "reverify" if is_reverify else "monitoring",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
            })
            return

        # CHANGED:
        # Continuous monitoring should not count unstable movement frames as
        # identity mismatches. This runs only for normal monitoring, not for
        # quiz-entry or re-verify.
        unstable_frame = None
        if not is_reverify:
            unstable_checker = globals().get("detect_identity_unstable_frame")
            if callable(unstable_checker):
                unstable_frame = unstable_checker(attempt_key, payload)

        if unstable_frame:
            current_count = ATTEMPT_MISMATCH_COUNT.get(attempt_key, 0)
            reason = unstable_frame.get("reason") or "unstable_frame"

            emit("face_check_result", {
                "ok": True,
                "status": "monitoring_tolerated",
                "reason": reason,
                "verification_mode": "monitoring",
                "confidence": None,
                "confidence_percent": None,
                "face_count": face_count,
                "count": current_count,
                "mismatch_count": current_count,
                "mismatch_limit": MISMATCH_GRACE_COUNT,
                "action": "tolerated",
                "motion_details": unstable_frame.get("details", {}),
            })
            return

        # CHANGED:
        # Majority identity matching for both modes.
        #
        # Registered / re-verify:
        #   - normally 5 frontal embeddings
        #   - require 4 matches
        #
        # Continuous monitoring:
        #   - when full monitoring support is available, normally 7 embeddings
        #     = 5 frontal + 2 side-support
        #   - require 5 matches only when there are at least 7 monitoring embeddings
        #   - if side-support was skipped and only 5 or 6 monitoring embeddings exist,
        #     require 4 matches so monitoring does not become stricter than re-verify.
        stored_embedding_count = len(stored_embs or [])

        if embedding_source == "monitoring_embeddings" and stored_embedding_count >= 7:
            required_match_count_for_mode = FACE_VERIFY_MONITOR_MIN_MATCH_COUNT
            match_mode = "monitoring"
        else:
            required_match_count_for_mode = FACE_VERIFY_REGISTERED_MIN_MATCH_COUNT
            match_mode = "registered"

        embedding_candidates = payload.get("embedding_candidates") or []
        candidate_items = []

        if isinstance(embedding_candidates, list):
            for index, item in enumerate(embedding_candidates[:5], start=1):
                if isinstance(item, dict):
                    cand_embedding = item.get("embedding")
                    cand_source = item.get("source") or f"candidate_{index}"
                else:
                    cand_embedding = item
                    cand_source = f"candidate_{index}"

                if isinstance(cand_embedding, list) and len(cand_embedding) == 128:
                    candidate_items.append({
                        "embedding": cand_embedding,
                        "source": cand_source,
                        "yaw_ratio": item.get("yaw_ratio") or item.get("yawRatio"),
                    })

        if not candidate_items:
            candidate_items = [{
                "embedding": embedding,
                "source": "single_frame",
                "yaw_ratio": payload.get("yaw_ratio") or payload.get("yawRatio"),
            }]

        best_candidate = None

        # CHANGED:
        # Continuous monitoring now checks the full monitoring bank FIRST.
        # If that does not pass, it also checks the pose-specific bank
        # (front/left/right) when available. Re-verify remains front-only.
        pose_bank_cache = {}

        def _candidate_banks_for_pose(candidate_pose):
            """
            Return the embedding banks to try for this candidate.

            Re-verify:
              - Use only the registered/front embeddings.

            Continuous monitoring:
              1. Try the full monitoring_embeddings bank first.
              2. Then try the pose-specific bank when available.

            This does not change the distance formula, confidence threshold,
            or majority thresholds. It only changes the order and coverage of
            the comparison banks used during normal monitoring.
            """
            if is_reverify:
                return [{
                    "embs": stored_embs,
                    "source": embedding_source,
                    "required": required_match_count_for_mode,
                    "mode": match_mode,
                    "bank_label": "reverify_front",
                }]

            banks = []

            # First choice: full monitoring bank loaded above.
            # Example expected log:
            # source=monitoring_embeddings, samples=11, policy=monitoring
            if stored_embs:
                banks.append({
                    "embs": stored_embs,
                    "source": embedding_source,
                    "required": required_match_count_for_mode,
                    "mode": match_mode,
                    "bank_label": "full_monitoring_first",
                })

            pose = str(candidate_pose or "front").strip().lower()
            if pose not in ("front", "left", "right"):
                pose = "front"

            if pose not in pose_bank_cache:
                pose_embs, pose_source = _ws_get_pose_embedding_bank(firebase_uid, pose)
                pose_bank_cache[pose] = (pose_embs, pose_source)

            pose_embs, pose_source = pose_bank_cache.get(pose) or ([], "")

            if pose_embs:
                pose_count = len(pose_embs)
                pose_required = None

                if pose == "front":
                    # Same as registered front verification: usually 4/5.
                    pose_required = min(FACE_VERIFY_REGISTERED_MIN_MATCH_COUNT, pose_count)
                elif pose in ("left", "right"):
                    # Side banks normally have 3 samples, so require 2/3.
                    # If there are fewer than 2 samples, do not trust the side bank yet.
                    if pose_count >= 2:
                        pose_required = 2

                if pose_required is not None:
                    # Avoid adding the same bank twice when the pose loader falls
                    # back to the same registered bank/source.
                    duplicate_existing_bank = any(
                        bank["source"] == pose_source and len(bank["embs"] or []) == pose_count
                        for bank in banks
                    )
                    if not duplicate_existing_bank:
                        banks.append({
                            "embs": pose_embs,
                            "source": pose_source,
                            "required": pose_required,
                            "mode": "registered",
                            "bank_label": "pose_specific",
                        })

            return banks

        def _candidate_rank(candidate_record):
            """
            Rank candidates/banks without changing the actual pass/fail policy.

            A real match always wins. For non-matches, prefer the result that is
            closest to passing by majority ratio, then by confidence, then by
            lower distance. The bank priority keeps full monitoring ahead when
            everything else is effectively tied.
            """
            info = candidate_record["match_info"]
            required = max(1, int(info.get("required_match_count") or 1))
            matched_count = int(info.get("matched_count") or 0)
            majority_ratio = matched_count / required
            bank_priority = 1 if candidate_record.get("bank_label") == "full_monitoring_first" else 0

            return (
                1 if info.get("matched") else 0,
                majority_ratio,
                matched_count,
                float(info.get("confidence") or 0.0),
                bank_priority,
                -float(info.get("best_distance") or 999.0),
            )

        # CHANGED:
        # Count how many monitoring candidates are borderline-good in the same batch.
        # A single lucky borderline frame should not be tolerated, especially for
        # an unregistered user. We require at least 2 borderline candidates before
        # applying monitoring-only tolerance.
        borderline_candidate_count = 0

        for candidate_index, candidate_item in enumerate(candidate_items, start=1):
            candidate_pose = "front" if is_reverify else _ws_pose_from_yaw(
                candidate_item.get("yaw_ratio") or payload.get("yaw_ratio") or payload.get("yawRatio")
            )

            candidate_best_record = None
            candidate_is_borderline = False
            candidate_banks = _candidate_banks_for_pose(candidate_pose)

            for bank_index, bank in enumerate(candidate_banks, start=1):
                candidate_stored_embs = bank.get("embs") or []
                candidate_embedding_source = bank.get("source") or embedding_source
                candidate_required_count = bank.get("required") or required_match_count_for_mode
                candidate_match_mode = bank.get("mode") or match_mode
                bank_label = bank.get("bank_label") or "bank"

                candidate_match_info = face_match_passes_majority(
                    candidate_item["embedding"],
                    candidate_stored_embs,
                    min_match_count=candidate_required_count,
                    mode=candidate_match_mode,
                )

                if (
                    not is_reverify
                    and not candidate_match_info["matched"]
                    and candidate_match_info["matched_count"] >= max(1, candidate_match_info["required_match_count"] - 1)
                    and candidate_match_info["confidence"] >= FACE_VERIFY_CONFIDENCE_THRESHOLD
                ):
                    candidate_is_borderline = True

                print(
                    f"🔍 WS {check_label} candidate {candidate_index}/{len(candidate_items)} "
                    f"bank={bank_index}/{len(candidate_banks)}:{bank_label}, "
                    f"source={candidate_item['source']}, pose={candidate_pose}, embedding_source={candidate_embedding_source}, "
                    f"samples={len(candidate_stored_embs)}, distance={candidate_match_info['best_distance']:.4f}, "
                    f"confidence={candidate_match_info['confidence']:.2%}, "
                    f"matched={candidate_match_info['matched']}, "
                    f"majority={candidate_match_info['matched_count']}/{candidate_match_info['required_match_count']}, "
                    f"distances={candidate_match_info['distance_debug']}",
                    flush=True,
                )

                candidate_record = {
                    "index": candidate_index,
                    "source": candidate_item["source"],
                    "pose": candidate_pose,
                    "embedding_source": candidate_embedding_source,
                    "stored_count": len(candidate_stored_embs),
                    "match_mode": candidate_match_mode,
                    "bank_label": bank_label,
                    "match_info": candidate_match_info,
                }

                if candidate_best_record is None or _candidate_rank(candidate_record) > _candidate_rank(candidate_best_record):
                    candidate_best_record = candidate_record

                # Stop checking weaker banks once one bank already passes.
                if candidate_match_info["matched"]:
                    break

            if candidate_is_borderline:
                borderline_candidate_count += 1

            if candidate_best_record is None:
                continue

            if best_candidate is None or _candidate_rank(candidate_best_record) > _candidate_rank(best_candidate):
                best_candidate = candidate_best_record

            if candidate_best_record["match_info"].get("matched"):
                break

        if best_candidate is None:
            emit("face_check_result", {
                "ok": True,
                "status": "no_biometrics",
                "verification_mode": "reverify" if is_reverify else "monitoring",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
            })
            return

        match_info = best_candidate["match_info"]

        # CHANGED:
        # Final output should describe the pose bank that actually produced
        # the best candidate result.
        embedding_source = best_candidate.get("embedding_source", embedding_source)
        stored_embedding_count = int(best_candidate.get("stored_count", stored_embedding_count))
        match_mode = best_candidate.get("match_mode", match_mode)
        selected_monitor_pose = best_candidate.get("pose", "front")

        best_distance = match_info["best_distance"]
        matched = match_info["matched"]
        confidence = match_info["confidence"]
        matched_count = match_info["matched_count"]
        required_match_count = match_info["required_match_count"]
        distance_debug = match_info["distance_debug"]
        required_threshold = FACE_VERIFY_CONFIDENCE_THRESHOLD

        print(
            f"🔍 WS {check_label} face check: source={embedding_source}, "
            f"samples={stored_embedding_count}, distance={best_distance:.4f}, "
            f"confidence={confidence:.2%}, required={required_threshold:.0%}, "
            f"matched={matched}, majority={matched_count}/{required_match_count}, "
            f"candidate={best_candidate['index']}:{best_candidate['source']}, "
            f"pose={selected_monitor_pose}, stored_count={stored_embedding_count}, policy={match_mode}, "
            f"distances={distance_debug}, user={session.get('user_id')}",
            flush=True
        )

        # CHANGED:
        # Monitoring-only poor-quality tolerance.
        #
        # If the frame quality is obviously poor, do not count it as a face
        # mismatch. This prevents false blackouts caused by dim/blurred/off-centre
        # monitor frames. Quiz entry and re-verify are not affected.
        if not is_reverify and not matched:
            quality_reason, quality_metrics = _ws_monitoring_poor_quality_from_metrics(payload)
            if quality_reason:
                ATTEMPT_MISMATCH_COUNT[attempt_key] = 0
                ATTEMPT_NO_FACE_COUNT[attempt_key] = 0
                ATTEMPT_MULTI_FACE_COUNT[attempt_key] = 0

                print(
                    f"↪️ WS poor-quality monitoring frame tolerated: "
                    f"attempt={attempt_key}, reason={quality_reason}, metrics={quality_metrics}, "
                    f"distance={best_distance:.4f}, confidence={confidence:.2%}, "
                    f"majority={matched_count}/{required_match_count}",
                    flush=True,
                )

                emit("face_check_result", {
                    "ok": True,
                    "status": "monitoring_tolerated",
                    "reason": "poor_quality_monitoring_frame",
                    "quality_reason": quality_reason,
                    "quality_metrics": quality_metrics,
                    "comparison": embedding_source,
                    "verification_mode": "monitoring",
                    "monitor_pose": selected_monitor_pose,
                    "threshold_percent": int(required_threshold * 100),
                    "best_distance": round(float(best_distance), 4),
                    "matched_count": matched_count,
                    "required_match_count": required_match_count,
                    "stored_embedding_count": stored_embedding_count,
                    "match_policy_mode": match_mode,
                    "all_distances": distance_debug,
                    "confidence": round(float(confidence), 4),
                    "confidence_percent": round(float(confidence) * 100, 2),
                    "face_count": face_count,
                    "count": 0,
                    "mismatch_count": 0,
                    "mismatch_limit": MISMATCH_GRACE_COUNT,
                    "action": "tolerated",
                })
                return

        # CHANGED:
        # Monitoring-only borderline tolerance.
        #
        # Quiz entry and re-verify remain strict: they still require full majority.
        # During continuous monitoring, a correct user may briefly match 3/4
        # because the hidden/side monitoring camera frame is less controlled.
        #
        # If the best face confidence still passes the 85% policy and the
        # majority agreement is only short by one, treat it as a tolerated
        # monitoring frame instead of counting toward blackout.
        #
        # This does NOT change:
        # - the 85% threshold
        # - the distance formula
        # - the majority requirement
        # It only prevents one-frame borderline monitoring checks from causing
        # accumulated false face_mismatch blackouts.
        if (
            not is_reverify
            and not matched
            and matched_count >= max(1, required_match_count - 1)
            and confidence >= FACE_VERIFY_CONFIDENCE_THRESHOLD
            # CHANGED:
            # Require repeated evidence within the same monitoring batch.
            # One borderline candidate is not enough because an unregistered
            # user may occasionally get one lucky 3/4 frame.
            and borderline_candidate_count >= 2
        ):
            ATTEMPT_MISMATCH_COUNT[attempt_key] = 0
            ATTEMPT_NO_FACE_COUNT[attempt_key] = 0
            ATTEMPT_MULTI_FACE_COUNT[attempt_key] = 0

            print(
                f"↪️ WS borderline monitoring frame tolerated: "
                f"attempt={attempt_key}, distance={best_distance:.4f}, "
                f"confidence={confidence:.2%}, majority={matched_count}/{required_match_count}, "
                f"borderline_candidates={borderline_candidate_count}",
                flush=True,
            )

            emit("face_check_result", {
                "ok": True,
                "status": "monitoring_tolerated",
                "reason": "borderline_identity_monitoring",
                "comparison": embedding_source,
                "verification_mode": "monitoring",
                "threshold_percent": int(required_threshold * 100),
                "best_distance": round(float(best_distance), 4),
                "matched_count": matched_count,
                "required_match_count": required_match_count,
                "stored_embedding_count": stored_embedding_count,
                "match_policy_mode": match_mode,
                "all_distances": distance_debug,
                "confidence": round(float(confidence), 4),
                "confidence_percent": round(float(confidence) * 100, 2),
                "face_count": face_count,
                "count": 0,
                "mismatch_count": 0,
                "mismatch_limit": MISMATCH_GRACE_COUNT,
                "action": "tolerated",
                "borderline_candidate_count": borderline_candidate_count,
            })
            return

        if not matched:
            ATTEMPT_MISMATCH_COUNT[attempt_key] = ATTEMPT_MISMATCH_COUNT.get(attempt_key, 0) + 1
            ATTEMPT_NO_FACE_COUNT[attempt_key] = 0
            ATTEMPT_MULTI_FACE_COUNT[attempt_key] = 0
            if 'ATTEMPT_MATCH_RECOVERY_COUNT' in globals():
                ATTEMPT_MATCH_RECOVERY_COUNT[attempt_key] = 0

            current_count = ATTEMPT_MISMATCH_COUNT[attempt_key]
            print(f"⚠️ WS mismatch count {current_count}/{MISMATCH_GRACE_COUNT} for attempt {attempt_key}", flush=True)

            if current_count < MISMATCH_GRACE_COUNT:
                emit("face_check_result", {
                    "ok": True,
                    "status": "mismatch",
                    "reason": "below_threshold",
                    "comparison": embedding_source,
                    "verification_mode": "reverify" if is_reverify else "monitoring",
                    "monitor_pose": selected_monitor_pose if not is_reverify else "front",
                    "threshold_percent": int(required_threshold * 100),
                    "best_distance": round(float(best_distance), 4),
                    "matched_count": matched_count,
                    "required_match_count": required_match_count,
                    "stored_embedding_count": stored_embedding_count,
                    "match_policy_mode": match_mode,
                    "all_distances": distance_debug,
                    "confidence": round(float(confidence), 4),
                    "confidence_percent": round(float(confidence) * 100, 2),
                    "face_count": face_count,
                    "count": current_count,
                    "mismatch_count": current_count,
                    "mismatch_limit": MISMATCH_GRACE_COUNT,
                    "action": "tolerated",
                })
                return

            _log_violation("face_mismatch")
            ATTEMPT_BLACKOUT_STATE[attempt_key] = True
            ATTEMPT_MISMATCH_COUNT[attempt_key] = 0

            ws_payload = {
                "attempt_id": attempt_key,
                "class_id": class_id,
                "quiz_id": quiz_id,
                "event_type": "blackout_on",
                "violation_type": "face_mismatch",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "face_count": face_count,
                "comparison": embedding_source,
                "verification_mode": "reverify" if is_reverify else "monitoring",
                "monitor_pose": selected_monitor_pose if not is_reverify else "front",
                "threshold_percent": int(required_threshold * 100),
                "best_distance": round(float(best_distance), 4),
                "matched_count": matched_count,
                "required_match_count": required_match_count,
                "stored_embedding_count": stored_embedding_count,
                "match_policy_mode": match_mode,
                "all_distances": distance_debug,
                "confidence": round(float(confidence), 4),
                "confidence_percent": round(float(confidence) * 100, 2),
            }

            print(f"🚨 Emitting student blackout_on (WS): {ws_payload}", flush=True)
            _emit_student_blackout_on(attempt_key, ws_payload)

            if class_id and quiz_id:
                print(f"🚨 Emitting instructor violation_alert (WS): room=class_{class_id}_quiz_{quiz_id}", flush=True)
                _emit_instructor_violation_alert(class_id, quiz_id, ws_payload)
            else:
                print(f"⚠️ WS skipped instructor emit because class_id or quiz_id missing. class_id={class_id}, quiz_id={quiz_id}", flush=True)

        else:
            ATTEMPT_MISMATCH_COUNT[attempt_key] = 0
            ATTEMPT_NO_FACE_COUNT[attempt_key] = 0
            ATTEMPT_MULTI_FACE_COUNT[attempt_key] = 0

            # CHANGED: Optional tolerance-based motion monitoring.
            # This does not change face distance calculation. It runs only during
            # normal continuous monitoring, not during re-verify.
            motion_event = None
            if not is_reverify:
                motion_event = detect_tolerant_motion_event(attempt_key, payload)

            if motion_event:
                _log_violation(motion_event["violation_type"])
                motion_payload = {
                    "attempt_id": attempt_key,
                    "class_id": class_id,
                    "quiz_id": quiz_id,
                    "event_type": motion_event["action"],
                    "violation_type": motion_event["violation_type"],
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "face_count": face_count,
                    "confidence": round(float(confidence), 4),
                    "confidence_percent": round(float(confidence) * 100, 2),
                    "motion_details": motion_event.get("details", {}),
                }
                if motion_event["action"] == "warning":
                    _emit_student_warning(attempt_key, motion_payload)
                else:
                    ATTEMPT_BLACKOUT_STATE[attempt_key] = True
                    _emit_student_blackout_on(attempt_key, motion_payload)
                if class_id and quiz_id:
                    _emit_instructor_violation_alert(class_id, quiz_id, motion_payload)
                if motion_event["action"] == "blackout_on":
                    emit("face_check_result", {
                        "ok": True,
                        "status": "motion_violation",
                        "confidence": round(float(confidence), 4),
                        "confidence_percent": round(float(confidence) * 100, 2),
                        "face_count": face_count,
                        "action": "blackout_on",
                        "violation_type": motion_event["violation_type"],
                    })
                    return
            was_paused = ATTEMPT_BLACKOUT_STATE.get(attempt_key, False)

            if was_paused:
                ws_payload = {
                    "attempt_id": attempt_key,
                    "class_id": class_id,
                    "quiz_id": quiz_id,
                    "event_type": "blackout_off",
                    "violation_type": "face_match",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "face_count": face_count,
                    "confidence": round(float(confidence), 4),
                    "confidence_percent": round(float(confidence) * 100, 2),
                }
                print(f"✅ Emitting student blackout_off (WS): {ws_payload}", flush=True)
                _emit_student_blackout_off(attempt_key, ws_payload)
                ATTEMPT_BLACKOUT_STATE[attempt_key] = False
            else:
                print(f"✅ WS face match (no blackout_off needed)", flush=True)

        status = "match" if matched else "mismatch"

        emit("face_check_result", {
            "ok": True,
            "status": status,
            "comparison": embedding_source,
            "monitor_pose": selected_monitor_pose if not is_reverify else "front",
            "threshold_percent": int(required_threshold * 100),
            "best_distance": round(float(best_distance), 4),
            "matched_count": matched_count,
            "required_match_count": required_match_count,
            "stored_embedding_count": stored_embedding_count,
            "match_policy_mode": match_mode,
            "all_distances": distance_debug,
            "confidence": round(float(confidence), 4),
            "confidence_percent": round(float(confidence) * 100, 2),
            "face_count": face_count,
            "mismatch_count": 0 if matched else MISMATCH_GRACE_COUNT,
            "mismatch_limit": MISMATCH_GRACE_COUNT,
            "action": "ok" if matched else "blackout_on",
        })

    except Exception as e:
        print(f"❌ face_check_embedding socket handler error: {str(e)}", flush=True)
        emit("face_check_result", {
            "ok": False,
            "status": "error",
            "message": str(e)
        })
