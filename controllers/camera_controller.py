"""Camera streaming, registration capture, and capture status routes."""

from ._shared import _set_liveness_running, load_app_context


load_app_context(globals())



def _pending_monitor_store_key(ts_key: str) -> str:
    """
    CHANGED: Separate temporary key for monitoring-support embeddings.
    Front registration embeddings still use the normal ts_key.
    """
    return f"{str(ts_key)}:monitor"


def _store_monitor_side_support_embeddings(ts_key: str, state: dict) -> int:
    """
    CHANGED: Silently stores at most one left and one right support embedding
    from liveness turn frames already collected during registration.

    These are NOT counted as required registration samples. They are stored
    under a separate pending key so strict quiz-entry verification can continue
    using only the normal front-facing registration samples.
    """
    if not ts_key:
        return 0

    side_frames = (state or {}).get("side_enrollment_frames") or {}
    if not isinstance(side_frames, dict) or not side_frames:
        return 0

    monitor_ts_key = _pending_monitor_store_key(ts_key)
    existing_support_count = _pending_store_get_count(monitor_ts_key)
    remaining_support_needed = max(0, 2 - existing_support_count)
    if remaining_support_needed <= 0:
        return 0

    saved_support_count = 0

    for pose in ("left", "right"):
        if saved_support_count >= remaining_support_needed:
            break

        side_frame = side_frames.get(pose)
        if side_frame is None:
            continue

        face_crop, face_box, crop_err = prepare_face_crop_from_frame(side_frame, pad_ratio=0.20)
        if crop_err:
            print(
                f"[SIDE-SUPPORT-EMBEDDING] pose={pose} skipped crop_err={crop_err}",
                flush=True,
            )
            continue

        emb, err = generate_embedding(face_crop)
        if err:
            print(
                f"[SIDE-SUPPORT-EMBEDDING] pose={pose} skipped embedding_err={err}",
                flush=True,
            )
            continue

        emb_list = np.asarray(emb, dtype=np.float32).reshape(-1).tolist()
        if len(emb_list) != 128:
            print(
                f"[SIDE-SUPPORT-EMBEDDING] pose={pose} skipped invalid_len={len(emb_list)}",
                flush=True,
            )
            continue

        _pending_store_put(monitor_ts_key, emb_list)
        saved_support_count += 1
        print(
            f"[SIDE-SUPPORT-EMBEDDING] pose={pose} saved "
            f"support_progress={existing_support_count + saved_support_count}/2",
            flush=True,
        )

    return saved_support_count


@app.route("/camera")
def camera():
    mode = (request.args.get("mode") or "").strip().lower()

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

    if not session.get("logged_in"):
        return redirect_with_msg("/login", "Please log in first.")

    session["camera_mode"] = "quiz"
    return render_template("camera.html", mode=mode)

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

        samples_to_save = min(1, remaining_needed)
        enrollment_frames = state.get("enrollment_frames") or []
        candidate_frames = [live_frame]
        candidate_frames.extend(frame for frame in enrollment_frames if frame is not live_frame)

        saved_count = 0
        last_error = None
        for sample_frame in candidate_frames:
            if saved_count >= samples_to_save:
                break

            face_crop, face_box, crop_err = prepare_face_crop_from_frame(sample_frame, pad_ratio=0.20)
            if crop_err:
                last_error = crop_err
                continue

            emb, err = generate_embedding(face_crop)
            if err:
                last_error = "Failed to generate embedding. Please try again."
                continue

            emb_list = np.asarray(emb, dtype=np.float32).reshape(-1).tolist()
            if len(emb_list) != 128:
                last_error = "Invalid embedding length. Please capture again."
                continue

            if saved_count == 0:
                cv2.imwrite(os.path.join(RECOG_FOLDER, "recognized.png"), sample_frame)
            _pending_store_put(ts_key, emb_list)
            saved_count += 1

        if saved_count > 0:
            _store_monitor_side_support_embeddings(ts_key, state)

        if saved_count == 0:
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

    # CHANGED: Append this embedding to the pending store list
    _pending_store_put(ts_key, emb_list)

    # CHANGED: Side support embeddings are collected silently from already
    # validated left/right liveness frames, and stored separately for monitoring.
    _store_monitor_side_support_embeddings(ts_key, state)

    captures_done = _pending_store_get_count(ts_key)
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

    if isinstance(sequence_payload, (dict, list)):
        sequence_data = json.dumps(sequence_payload)
    elif isinstance(sequence_payload, str):
        sequence_data = sequence_payload
    else:
        return fail("Missing live camera frames. Please try again.", 400)

    ok_phase, phase_data, err = validate_browser_liveness_phase(
        sequence_data,
        phase,
        _get_stream_key(),
    )
    if not ok_phase:
        return fail(err or "Liveness step failed. Please try again.", 400)

    return ok(phase_data or {"phase": phase}, f"{phase.replace('_', ' ').title()} validated")

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
    DOMContentLoaded:

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
