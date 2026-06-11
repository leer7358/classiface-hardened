"""Socket.IO event handlers for quiz monitoring."""

from ._shared import _set_liveness_running, load_app_context


load_app_context(globals())


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

@socketio.on("student_monitor_frame")
def handle_student_monitor_frame(data):  # CHANGED
    try:
        import base64  # CHANGED

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
    - Uses the same calibrated 85% confidence helper as REST quiz verification.
    - Adds backend grace counters so transient bad frames do not pause immediately.
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
        initial_verify = bool(payload.get("initial_verify"))

        if not attempt_id:
            emit("face_check_result", {
                "ok": False,
                "status": "error",
                "message": "attempt_id is required"
            })
            return

        attempt_key = str(attempt_id)

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
        # Use temporary backend embedding cache instead of repeatedly reading
        # and decrypting Firebase embeddings during WebSocket monitoring.
        # This improves monitoring performance without changing cosine distance,
        # confidence, threshold, or face recognition logic.
        stored_embs = fb_get_decrypted_embeddings_cached(firebase_uid)

        if not stored_embs:
            emit("face_check_result", {
                "ok": True,
                "status": "no_biometrics",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
            })
            return

        best_distance = _best_distance_against_embeddings(embedding, stored_embs)

        matched, confidence = face_match_passes_85(best_distance)

        print(
            f"🔍 WS Face check: distance={best_distance:.4f}, confidence={confidence:.2%}, "
            f"matched={matched}, user={session.get('user_id')}",
            flush=True
        )

        if not matched:
            ATTEMPT_MISMATCH_COUNT[attempt_key] = ATTEMPT_MISMATCH_COUNT.get(attempt_key, 0) + 1
            ATTEMPT_NO_FACE_COUNT[attempt_key] = 0
            ATTEMPT_MULTI_FACE_COUNT[attempt_key] = 0

            current_count = ATTEMPT_MISMATCH_COUNT[attempt_key]
            print(f"⚠️ WS mismatch count {current_count}/{MISMATCH_GRACE_COUNT} for attempt {attempt_key}", flush=True)

            if initial_verify:
                emit("face_check_result", {
                    "ok": True,
                    "status": "mismatch",
                    "reason": "initial_identity_failed",
                    "confidence": round(float(confidence), 4),
                    "confidence_percent": round(float(confidence) * 100, 2),
                    "face_count": face_count,
                    "count": current_count,
                    "mismatch_count": current_count,
                    "mismatch_limit": 1,
                    "action": "blocked",
                })
                return

            if current_count < MISMATCH_GRACE_COUNT:
                emit("face_check_result", {
                    "ok": True,
                    "status": "monitoring_tolerated",
                    "reason": "temporary_mismatch",
                    "confidence": round(float(confidence), 4),
                    "confidence_percent": round(float(confidence) * 100, 2),
                    "face_count": face_count,
                    "count": current_count,
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
            # This does not change face matching, distance, cosine, or 85% confidence logic.
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
            "confidence": round(float(confidence), 4),
            "confidence_percent": round(float(confidence) * 100, 2),
            "face_count": face_count,
        })

    except Exception as e:
        print(f"❌ face_check_embedding socket handler error: {str(e)}", flush=True)
        emit("face_check_result", {
            "ok": False,
            "status": "error",
            "message": str(e)
        })
