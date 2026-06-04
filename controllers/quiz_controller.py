"""Student quiz verification, attempt lifecycle, proctoring, and grading routes."""

from ._shared import _set_liveness_running, load_app_context


load_app_context(globals())


@app.route("/start-quiz/<quiz_id>")
def start_quiz(quiz_id):
    guard = student_required()
    if guard:
        return guard

    class_id = (session.get("active_class_id") or "").strip()
    if not class_id:
        return redirect_with_msg("/class-lists", "Please select your class first.")

    row = pg_get_quiz_by_id(str(quiz_id))
    if not row:
        return redirect_with_msg(_stud_home_url(), "Quiz not found.")

    if str(row.get("class_id") or "") != str(class_id):
        return redirect_with_msg(_stud_home_url(), "Quiz not found for this class.")

    if not row.get("is_active", False):
        return redirect_with_msg(
            f"/stud-class-home/{class_id}",
            "This quiz is not activated yet. Please wait for your instructor to activate it."
        )

    now_dt = app_now()
    sess = pg_get_active_session_for_date(str(class_id), now_dt.date())

    if not sess:
        return redirect_with_msg(
            f"/stud-class-home/{class_id}",
            "Quiz is not available yet (no active session range for today)."
        )

    quiz_available, quiz_status = compute_quiz_availability(now_dt, sess)

    if not quiz_available:
        if quiz_status == "not_started":
            msg = "Quiz is not available yet. Please wait for the session to begin."
        elif quiz_status == "closed":
            msg = "Quiz is already closed."
        else:
            msg = "Quiz is not available at this time."
        return redirect_with_msg(f"/stud-class-home/{class_id}", msg)

    session["pending_quiz_id"] = str(quiz_id)
    session["quiz_verified"] = False
    session["verified_name"] = ""

    return redirect(url_for("quiz_verify"))

@app.route("/quiz_verify")
def quiz_verify():
    guard = student_required()
    if guard:
        return guard
    if not session.get("active_class_id"):
        return redirect_with_msg("/class-lists", "Please select your class first.")
    return render_template("quiz_verify.html")

@app.route("/quiz_capture", methods=["POST"])
def quiz_capture():
    """
    CHANGED: Now retrieves ALL stored embeddings for the user from Firebase
    (embeddings_enc_list) and compares the live face against each one,
    using the best (lowest) distance for the match decision.

    CHANGED: Verification decision is based on an 85% confidence threshold,
    but confidence is now CALIBRATED from distance instead of using
    confidence = 1 - distance directly.

    CHANGED: Embedding is now generated from the selected face crop only
    instead of the full frame, to reduce wrong-face / noisy-frame issues.
    """
    global is_liveness_running

    stream_key = _get_stream_key()
    state = _reset_liveness_state(stream_key)

    guard = student_required()
    if guard:
        return guard

    class_id = (session.get("active_class_id") or "").strip()
    if not class_id:
        return redirect_with_msg("/class-lists", "Please select your class first.")

    # CHANGED: fixed NameError by using the compatibility wrapper
    sess = pg_get_today_session(class_id)
    if not sess:
        return redirect_with_msg(
            "/quiz_verify",
            "No class session set for today. Ask your instructor to set the time window."
        )

    pg_user_id = str(session["user_id"])
    firebase_uid = str(session.get("firebase_uid") or "")
    quiz_id = (session.get("pending_quiz_id") or "").strip()

    if not quiz_id:
        return redirect_with_msg(_stud_home_url(), "Please select a quiz first.")

    qrow = pg_get_quiz_by_id(quiz_id)
    if not qrow or str(qrow.get("class_id") or "") != str(class_id):
        return redirect_with_msg(_stud_home_url(), "Quiz not found for this class.")

    if not qrow.get("is_active", False):
        return redirect_with_msg(
            f"/stud-class-home/{class_id}",
            "This quiz has been deactivated by your instructor."
        )

    now_dt = app_now()
    quiz_available, _ = compute_quiz_availability(now_dt, sess)
    if not quiz_available:
        return redirect_with_msg(
            f"/stud-class-home/{class_id}",
            "Quiz is not available at this time (outside session window)."
        )

    if not firebase_uid:
        return redirect_with_msg("/quiz_verify", "Missing Firebase UID in session. Please login again.")

    enc_list = fb_get_embedding_enc(firebase_uid)
    if not enc_list:
        app.logger.error(f"No biometric data found in Firebase for user {_mask_uid(firebase_uid)}")
        return redirect_with_msg("/quiz_verify", "No biometric data found. Please re-register.")

    stored_embs = []
    for enc in enc_list:
        try:
            emb = decrypt_embedding(enc)
            if isinstance(emb, list) and len(emb) == 128:
                stored_embs.append(emb)
        except Exception:
            continue

    if not stored_embs:
        return redirect_with_msg("/quiz_verify", "Invalid biometric template. Please re-register.")

    frame_data = request.form.get("frame_data") or ""
    if frame_data:
        frame, frame_err = decode_browser_frame(frame_data)
        if frame_err:
            return redirect_with_msg("/quiz_verify", frame_err)

        face_crop, face_box, crop_err = prepare_face_crop_from_frame(frame, pad_ratio=0.20)
        if crop_err:
            return redirect_with_msg("/quiz_verify", crop_err)

        cv2.imwrite(os.path.join(RECOG_FOLDER, "recognized.png"), face_crop)

        emb, err = generate_embedding(face_crop)
        if err:
            return redirect_with_msg("/quiz_verify", err)

        emb_list = np.asarray(emb, dtype=np.float32).reshape(-1).tolist()
        if len(emb_list) != 128:
            return redirect_with_msg("/quiz_verify", "Embedding error. Please try again.")

        best_distance = _best_distance_against_embeddings(emb_list, stored_embs)
        MAX_DISTANCE = 2.10
        confidence = max(0.0, 1.0 - (best_distance / MAX_DISTANCE)) if best_distance < 999.0 else 0.0
        CONFIDENCE_THRESHOLD = 0.85

        if confidence < CONFIDENCE_THRESHOLD:
            session["quiz_verified"] = False
            return redirect_with_msg("/quiz_verify", "Face does not match your registration.")

        session["quiz_verified"] = True
        session["verified_name"] = session.get("student_name", "")

        now_t = app_now().time().replace(second=0, microsecond=0)
        status = compute_attendance_status(
            now_t,
            sess.get("present_start"),
            sess.get("present_until"),
            sess.get("late_start"),
            sess.get("late_until"),
            sess.get("session_end"),
        )

        try:
            ok_att, att_msg = pg_mark_attendance_for_session(
                user_id=pg_user_id,
                class_id=class_id,
                session_id=str(sess["id"]),
                status=status,
                quiz_id=str(quiz_id),
            )
        except Exception as e:
            print("ATTENDANCE INSERT FAILED:", str(e), flush=True)
            app.logger.error(f"Attendance database error: {type(e).__name__}")
            return redirect_with_msg("/quiz_verify", "An error occurred. Please try again.")

        return redirect_with_msg(
            url_for("stud_quiz_session", quiz_id=str(quiz_id)),
            f"Verified. {att_msg}."
        )

    cap = _init_camera()
    state["liveness_preview_frame"] = None  # CHANGED

    direction, blinks_required = _new_challenge()

    _set_liveness_running(True)
    try:
        state["live_instruction"] = "Starting identification..."  # CHANGED
        state["live_subtext"] = f"Blink {blinks_required} times + turn {direction}"  # CHANGED
        _flush_camera(cap, n=10)
        ok_live, frame, reason = pass_liveness_from_camera(cap, direction, blinks_required, stream_key)
    finally:
        _set_liveness_running(False)
        state["liveness_preview_frame"] = None  # CHANGED

    if not ok_live or frame is None:
        state["live_instruction"] = "Verification failed"  # CHANGED
        state["live_subtext"] = reason or "Liveness failed"  # CHANGED
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/quiz_verify", f"Liveness failed: {reason}")

    # ============================================================
    # QUIZ STABILITY CHECK
    # Require the student to remain still before generating the
    # verification embedding. This keeps quiz verification aligned
    # with registration while staying usable for slower devices.
    # ============================================================
    state["live_instruction"] = "Securing identity"
    state["live_subtext"] = "Hold still..."

    stable_start = time.time()
    wait_started = time.time()
    prev_center_x = None
    prev_center_y = None
    stable_frame = frame

    required_stable_seconds = 2.0
    movement_threshold = 25
    max_stability_wait_seconds = 8.0

    while True:
        success, current_frame = cap.read()

        if not success or current_frame is None:
            stable_start = time.time()
            if time.time() - wait_started > max_stability_wait_seconds:
                _release_camera_if_idle(force=True)
                return redirect_with_msg(
                    "/quiz_verify",
                    "Could not confirm stillness. Please try again."
                )
            time.sleep(0.05)
            continue

        faces_now = detect_faces(current_frame)
        face_box_now, err_now = pick_single_face(faces_now, current_frame)

        if err_now or face_box_now is None:
            stable_start = time.time()
            if time.time() - wait_started > max_stability_wait_seconds:
                _release_camera_if_idle(force=True)
                return redirect_with_msg(
                    "/quiz_verify",
                    "Please keep one clear face in the camera frame."
                )
            time.sleep(0.05)
            continue

        x_now, y_now, w_now, h_now = face_box_now
        center_x = x_now + (w_now / 2)
        center_y = y_now + (h_now / 2)

        if prev_center_x is not None:
            movement = (
                abs(center_x - prev_center_x)
                + abs(center_y - prev_center_y)
            )

            # Reset the timer only for obvious movement.
            # This tolerates small webcam jitter and natural breathing.
            if movement > movement_threshold:
                stable_start = time.time()

        prev_center_x = center_x
        prev_center_y = center_y
        stable_frame = current_frame

        stable_elapsed = time.time() - stable_start
        remaining = max(0.0, required_stable_seconds - stable_elapsed)
        state["live_subtext"] = f"Hold still... {remaining:.1f}s"

        if stable_elapsed >= required_stable_seconds:
            break

        if time.time() - wait_started > max_stability_wait_seconds:
            _release_camera_if_idle(force=True)
            return redirect_with_msg(
                "/quiz_verify",
                "Too much movement detected. Please hold still and try again."
            )

        time.sleep(0.05)

    # Use the latest stable frame for face crop and embedding generation.
    frame = stable_frame

    face_box, face_err = pick_single_face(detect_faces(frame), frame)
    if face_err or face_box is None:
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/quiz_verify", face_err or "No usable face detected.")

    x, y, w, h = face_box

    pad_x = int(w * 0.20)
    pad_y = int(h * 0.20)

    x1 = max(0, x - pad_x)
    y1 = max(0, y - pad_y)
    x2 = min(frame.shape[1], x + w + pad_x)
    y2 = min(frame.shape[0], y + h + pad_y)

    face_crop = frame[y1:y2, x1:x2]

    if face_crop is None or face_crop.size == 0:
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/quiz_verify", "Invalid face crop. Please try again.")

    if face_crop.shape[0] < 40 or face_crop.shape[1] < 40:
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/quiz_verify", "Face too small. Please move closer and try again.")

    cv2.imwrite(os.path.join(RECOG_FOLDER, "recognized.png"), face_crop)

    emb, err = generate_embedding(face_crop)
    if err:
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/quiz_verify", err)

    emb_list = np.asarray(emb, dtype=np.float32).reshape(-1).tolist()
    if len(emb_list) != 128:
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/quiz_verify", "Embedding error. Please try again.")

    best_distance = _best_distance_against_embeddings(emb_list, stored_embs)

    MAX_DISTANCE = 2.10
    confidence = max(0.0, 1.0 - (best_distance / MAX_DISTANCE)) if best_distance < 999.0 else 0.0

    CONFIDENCE_THRESHOLD = 0.85

    print(
        f"   Best face distance: {best_distance:.4f}, "
        f"confidence: {confidence:.2%}, "
        f"required: {int(CONFIDENCE_THRESHOLD * 100)}%",
        flush=True
    )

    if confidence >= CONFIDENCE_THRESHOLD:
        # =========================
        # CHANGED: SUCCESS OVERLAY
        # =========================
        state["live_instruction"] = "Verification successful"  # CHANGED
        state["live_subtext"] = "Preparing your quiz"  # CHANGED
        # =========================

        session["quiz_verified"] = True
        session["verified_name"] = session.get("student_name", "")

        now_t = app_now().time().replace(second=0, microsecond=0)
        status = compute_attendance_status(
            now_t,
            sess.get("present_start"),
            sess.get("present_until"),
            sess.get("late_start"),
            sess.get("late_until"),
            sess.get("session_end"),
        )

        try:
            ok_att, att_msg = pg_mark_attendance_for_session(
                user_id=pg_user_id,
                class_id=class_id,
                session_id=str(sess["id"]),
                status=status,
                quiz_id=str(quiz_id),
            )
            print("✅ ATTENDANCE:", att_msg, flush=True)
        except Exception as e:
            print("❌ ATTENDANCE INSERT FAILED:", str(e), flush=True)
            _release_camera_if_idle(force=True)
            app.logger.error(f"Attendance database error: {type(e).__name__}")
            return redirect_with_msg("/quiz_verify", "An error occurred. Please try again.")

        _release_camera_if_idle(force=True)
        return redirect_with_msg(
            url_for("stud_quiz_session", quiz_id=str(quiz_id)),
            f"✅ Verified. {att_msg}."
        )

    session["quiz_verified"] = False
    _release_camera_if_idle(force=True)
    return redirect_with_msg("/quiz_verify", "❌ Face does not match your registration.")

@app.route("/stud-quiz-session/<quiz_id>")
def stud_quiz_session(quiz_id):
    guard = student_required()
    if guard:
        return guard
    if not session.get("quiz_verified"):
        return redirect(url_for("quiz_verify"))

    class_id = (session.get("active_class_id") or "").strip()
    if not class_id:
        return redirect_with_msg("/class-lists", "Please select your class first.")

    sess = pg_get_today_session(class_id)
    quiz_available, _ = compute_quiz_availability(app_now(), sess)
    if not quiz_available:
        return redirect_with_msg(f"/stud-class-home/{class_id}", "Quiz session is closed (outside session window).")

    row = pg_get_quiz_by_id(str(quiz_id))
    if not row or str(row.get("class_id") or "") != str(class_id):
        return redirect_with_msg(_stud_home_url(), "Quiz not found.")

    if not row.get("is_active", False):
        return redirect_with_msg(f"/stud-class-home/{class_id}", "This quiz has been deactivated by your instructor.")

    # NEW: Check if student has exceeded attempts limit
    attempts_type = row.get("attempts_type", "unlimited")
    attempts_limit = row.get("attempts_limit")
    user_id = str(session.get("user_id"))
    
    if attempts_type == "limited" and attempts_limit and attempts_limit > 0:
        try:
            with pg_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) as attempt_count
                    FROM quiz_attempts
                    WHERE user_id = %s AND quiz_id = %s AND submitted_at IS NOT NULL;
                    """,
                    (user_id, str(quiz_id)),
                )
                result = cur.fetchone()
                attempt_count = result["attempt_count"] if result else 0
                
                if attempt_count >= attempts_limit:
                    return redirect_with_msg(
                        f"/stud-class-home/{class_id}",
                        f"❌ You have exhausted your {attempts_limit} attempt(s) for this quiz."
                    )
        except Exception as e:
            app.logger.warning(f"Error checking attempts limit: {type(e).__name__}: {str(e)}")

    qjson = row.get("questions_json") or []

    if isinstance(qjson, str):
        try:
            qjson = json.loads(qjson)
        except Exception:
            qjson = []

    if isinstance(qjson, dict):
        questions = qjson.get("questions", [])
    elif isinstance(qjson, list):
        questions = qjson
    else:
        questions = []

    if not isinstance(questions, list):
        questions = []

    quiz_json = {
        "title": row.get("title") or "Quiz",
        "questions": questions
    }

    attempt_id = str(uuid.uuid4())
    time_limit_minutes = int(row.get("time_limit_minutes") or 60)
    duration_seconds = time_limit_minutes * 60

    # NEW: Pass attempts info to template
    attempts_remaining = None
    if attempts_type == "limited" and attempts_limit and attempts_limit > 0:
        try:
            with pg_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) as attempt_count
                    FROM quiz_attempts
                    WHERE user_id = %s AND quiz_id = %s AND submitted_at IS NOT NULL;
                    """,
                    (user_id, str(quiz_id)),
                )
                result = cur.fetchone()
                attempt_count = result["attempt_count"] if result else 0
                attempts_remaining = max(0, attempts_limit - attempt_count)
        except:
            pass

    return render_template(
        "stud-quiz-session.html",
        quiz_id=str(quiz_id),
        attempt_id=attempt_id,
        duration_seconds=duration_seconds,
        quiz_json=quiz_json,
        # NEW: Pass attempts metadata
        attempts_type=attempts_type,
        attempts_limit=attempts_limit,
        attempts_remaining=attempts_remaining,
        # CHANGED: Tell the template that camera permission must be requested
        # via navigator.mediaDevices.getUserMedia() before monitoring begins.
        # The template JS must call getUserMedia(), wait for it to resolve, then
        # and only then start the monitoring interval. If the user denies,
        # camera_denied_message is shown and the quiz is blocked from continuing.
        camera_permission_required=True,  # CHANGED
        camera_denied_message=(  # CHANGED
            "Camera access is required to take this quiz. "  # CHANGED
            "Please allow camera access in your browser and reload the page."  # CHANGED
        ),  # CHANGED
    )

@app.route("/api/quiz-attempts/<attempt_id>/start", methods=["POST"])
def api_quiz_attempt_start(attempt_id):
    """
    Start a quiz attempt and generate one-time submission token.
    Token must be provided when submitting the quiz.
    """
    guard = student_required()
    if guard:
        return fail("Not logged in", 401)
    if not _require_csrf_json():
        return fail("CSRF failed", 400)

    data = request.get_json(silent=True) or {}
    quiz_id = (data.get("quizId") or "").strip()

    if not quiz_id:
        return fail("Missing quizId", 400)

    class_id = (session.get("active_class_id") or "").strip()

    try:
        qrow = pg_get_quiz_by_id(quiz_id)
    except Exception:
        qrow = None

    if not qrow or str(qrow.get("class_id") or "") != str(class_id):
        return fail("Quiz not found for this class", 404)

    if not qrow.get("is_active", False):
        return fail("This quiz has been deactivated by your instructor", 403)

    quiz_title = (qrow.get("title") or "Quiz").strip()
    total_points = int(qrow.get("total_points") or 0)
    user_id = str(session.get("user_id"))

    try:
        with pg_conn() as conn, conn.cursor() as cur:
            # 1) Check for an in-progress attempt (not submitted) - reuse it
            cur.execute(
                """
                SELECT attempt_id
                FROM quiz_attempts
                WHERE user_id = %s AND quiz_id = %s AND submitted_at IS NULL
                ORDER BY started_at DESC
                LIMIT 1;
                """,
                (user_id, quiz_id),
            )
            existing = cur.fetchone()

            if existing:
                existing_attempt_id = str(existing["attempt_id"])

                cur.execute(
                    """
                    UPDATE quiz_attempts
                    SET quiz_title   = %s,
                        total_points = %s,
                        started_at   = NOW(),
                        answers_json = '{}'::jsonb
                    WHERE attempt_id = %s;
                    """,
                    (quiz_title, total_points, existing_attempt_id),
                )

                conn.commit()
                
                # SECURITY: Generate fresh submission token for reused attempt
                submission_token = pg_generate_submission_token(existing_attempt_id, user_id, quiz_id)
                
                print(
                    f"✅ Reused existing in-progress attempt: attempt_id={existing_attempt_id}, quiz_id={quiz_id}, user_id={user_id}",
                    flush=True
                )
                return ok({
                    "started": True,
                    "attempt_id": existing_attempt_id,
                    "submission_token": submission_token
                }, "Attempt started")

            # 2) NEW: Check if student has exceeded attempts limit (only for completed attempts)
            attempts_type = qrow.get("attempts_type", "unlimited")
            attempts_limit = qrow.get("attempts_limit")
            
            if attempts_type == "limited" and attempts_limit and attempts_limit > 0:
                cur.execute(
                    """
                    SELECT COUNT(*) as attempt_count
                    FROM quiz_attempts
                    WHERE user_id = %s AND quiz_id = %s AND submitted_at IS NOT NULL;
                    """,
                    (user_id, quiz_id),
                )
                result = cur.fetchone()
                attempt_count = result["attempt_count"] if result else 0
                
                if attempt_count >= attempts_limit:
                    return fail(
                        f"You have exhausted your {attempts_limit} attempt(s) for this quiz",
                        403
                    )

            # 3) Get next attempt number
            cur.execute(
                """
                SELECT COALESCE(MAX(attempt_number), 0) + 1 as next_attempt_number
                FROM quiz_attempts
                WHERE user_id = %s AND quiz_id = %s;
                """,
                (user_id, quiz_id),
            )
            result = cur.fetchone()
            next_attempt_number = result["next_attempt_number"] if result else 1

            # 4) Insert a brand-new attempt
            cur.execute(
                """
                INSERT INTO quiz_attempts
                  (attempt_id, user_id, quiz_id, quiz_title, score, total_points, started_at, submitted_at, answers_json, attempt_number)
                VALUES (%s, %s, %s, %s, 0, %s, NOW(), NULL, '{}'::jsonb, %s);
                """,
                (
                    str(attempt_id),
                    user_id,
                    quiz_id,
                    quiz_title,
                    total_points,
                    next_attempt_number,
                ),
            )

            conn.commit()
            
            # SECURITY: Generate submission token for new attempt
            submission_token = pg_generate_submission_token(str(attempt_id), user_id, quiz_id)
            
            print(
                f"✅ New attempt created: attempt_id={attempt_id}, attempt_number={next_attempt_number}, quiz_id={quiz_id}, user_id={user_id}",
                flush=True
            )
            return ok({
                "started": True,
                "attempt_id": str(attempt_id),
                "submission_token": submission_token
            }, "Attempt started")

    except Exception as e:
        app.logger.error(f"Quiz attempt start failed: {type(e).__name__}: {str(e)}")
        return fail("Operation failed", 500)

@app.route("/api/quiz-attempts/<attempt_id>/violation", methods=["POST"])
def api_quiz_attempt_violation(attempt_id):
    guard = student_required()
    if guard:
        return fail("Not logged in", 401)
    if not _require_csrf_json():
        return fail("CSRF failed", 400)

    data = request.get_json(silent=True) or {}
    vtype = (data.get("type") or "").strip()
    ts = data.get("timestamp")
    time_remaining = data.get("timeRemaining")

    class_id = str(session.get("active_class_id") or "")
    quiz_id = str(data.get("quizId") or "")
    if not quiz_id:  # CHANGED
        quiz_id = str(data.get("quiz_id") or "")

    # CHANGED: Ensure violation table exists with correct schema before insert
    # CHANGED: violation table schema is verified once at app startup; avoid repeated checks here.

    try:
        with pg_conn() as conn, conn.cursor() as cur:
            # CHANGED: Removed user_id from INSERT — quiz_attempt_violations must NOT
            # store user_id directly. User is always resolved via:
            # quiz_attempt_violations.attempt_id -> quiz_attempts.user_id
            cur.execute(
                """
                INSERT INTO quiz_attempt_violations
                  (id, attempt_id, violation_type, timestamp_iso, time_remaining)
                VALUES (%s, %s, %s, %s, %s);
                """,
                (
                    str(uuid.uuid4()),
                    str(attempt_id),
                    vtype or "unknown",
                    ts,
                    time_remaining,
                ),
            )
            # NOTE: conn.commit() is critical; without it the row is never persisted
    except Exception as e:
        app.logger.error(f"Violation insert failed: {type(e).__name__}: {str(e)}")
        return fail("Operation failed", 500)

    ws_payload = {  # CHANGED
        "attempt_id": str(attempt_id),  # CHANGED
        "class_id": class_id,  # CHANGED
        "quiz_id": quiz_id,  # CHANGED
        "event_type": "warning",  # CHANGED
        "violation_type": vtype or "unknown",  # CHANGED
        "timestamp": ts,  # CHANGED
        "time_remaining": time_remaining,  # CHANGED
    }  # CHANGED

    print(f"🚨 Emitting student warning: {ws_payload}", flush=True)
    _emit_student_warning(str(attempt_id), ws_payload)

    if class_id and quiz_id:  # CHANGED
        print(f"🚨 Emitting instructor violation_alert: room=class_{class_id}_quiz_{quiz_id}", flush=True)
        _emit_instructor_violation_alert(class_id, quiz_id, ws_payload)
    else:  # CHANGED
        print(f"⚠️ Skipped instructor emit because class_id or quiz_id missing. class_id={class_id}, quiz_id={quiz_id}", flush=True)

    return ok({"saved": True}, "Violation recorded")

@app.route("/api/quiz-attempts/<attempt_id>/submit", methods=["POST"])
def api_quiz_attempt_submit(attempt_id):
    """
    SECURE SUBMISSION HANDLER with tamper detection and replay prevention.
    
    Security features:
    1. One-time submission tokens (prevent replay attacks)
    2. Server-side answer validation (prevent tampering)
    3. Server-side grading (never trust client score)
    4. Integrity checks (detect answer modification)
    5. Audit logging with IP address and timing
    """
    guard = student_required()
    if guard:
        return fail("Not logged in", 401)
    if not _require_csrf_json():
        return fail("CSRF failed", 400)

    data = request.get_json(silent=True) or {}
    quiz_id = (data.get("quizId") or "").strip() or "unknown"
    answers = data.get("answers") or {}
    submission_token = (data.get("submissionToken") or "").strip()
    user_id = str(session.get("user_id"))
    client_ip = pg_get_client_ip()
    user_agent = request.headers.get('User-Agent', 'unknown')[:500]

    # ========================================
    # STEP 1: REPLAY ATTACK PREVENTION
    # Check if attempt already submitted
    # ========================================
    if pg_check_replay_attack(user_id, str(attempt_id)):
        tamper_msg = "Duplicate submission detected (replay attack prevented)"
        pg_log_submission_attempt(
            attempt_id, user_id, quiz_id, submission_token,
            False, client_ip, user_agent, "", 0, 0,
            tamper_detected=True,
            tamper_reason="Replay attack: attempt already submitted"
        )
        return fail(tamper_msg, 403)

    # ========================================
    # STEP 2: SUBMISSION TOKEN VALIDATION
    # Verify one-time token
    # ========================================
    if not submission_token:
        pg_log_submission_attempt(
            attempt_id, user_id, quiz_id, "", False, client_ip, user_agent, "", 0, 0,
            tamper_detected=True, tamper_reason="Missing submission token"
        )
        return fail("Missing submission token", 400)
    
    token_valid, token_error = pg_validate_submission_token(str(attempt_id), submission_token)
    if not token_valid:
        pg_log_submission_attempt(
            attempt_id, user_id, quiz_id, submission_token, False, client_ip,
            user_agent, "", 0, 0, tamper_detected=True, tamper_reason=token_error
        )
        return fail(f"Invalid submission token: {token_error}", 403)

    # ========================================
    # STEP 3: LOAD QUIZ AND EXTRACT ANSWERS
    # Server-side authoritative source
    # ========================================
    quiz_title = "Quiz"
    quiz_questions = []
    correct_answers = {}
    
    try:
        qrow = pg_get_quiz_by_id(quiz_id)
        if qrow:
            quiz_title = (qrow.get("title") or "Quiz").strip()
            qjson = qrow.get("questions_json") or []

            if isinstance(qjson, str):
                try:
                    qjson = json.loads(qjson)
                except Exception:
                    qjson = []

            if isinstance(qjson, dict):
                qs = qjson.get("questions") or []
            elif isinstance(qjson, list):
                qs = qjson
            else:
                qs = []

            if isinstance(qs, list):
                quiz_questions = qs
                # CRITICAL: Extract correct answers server-side (never trust client)
                correct_answers = pg_extract_correct_answers(quiz_questions)
    except Exception as e:
        return fail(f"Quiz loading error: {str(e)}", 500)

    # ========================================
    # STEP 4: ANSWER INTEGRITY VALIDATION
    # Detect tampering attempts
    # ========================================
    integrity_valid, integrity_error = pg_validate_answers_integrity(
        answers, correct_answers, quiz_questions
    )
    if not integrity_valid:
        answers_hash = pg_compute_answers_hash(answers)
        pg_log_submission_attempt(
            attempt_id, user_id, quiz_id, submission_token, True, client_ip,
            user_agent, answers_hash, 0, 0,
            tamper_detected=True, tamper_reason=f"Answer integrity check failed: {integrity_error}"
        )
        return fail(f"Answer validation failed: {integrity_error}", 400)

    # ========================================
    # STEP 5: SERVER-SIDE GRADING
    # Never trust client-calculated scores
    # ========================================
    if not quiz_questions:
        score = 0
        total_points = 0
    else:
        def _norm(s):
            return (str(s).strip() if s is not None else "")

        def get_answer_for(qid):
            if qid is None:
                return None
            skey = str(qid)
            if skey in answers:
                return answers[skey]
            try:
                ikey = int(skey)
                return answers.get(ikey)
            except Exception:
                return None

        def grade_one(q, student_ans):
            qtype = _norm(q.get("type"))
            try:
                pts = int(q.get("points") or 0)
            except Exception:
                pts = 0
            if pts < 0:
                pts = 0

            if student_ans is None or student_ans == "":
                return 0, pts

            if qtype == "multiple-choice":
                correct = q.get("correctAnswers") or []
                if not isinstance(correct, list):
                    correct = []
                try:
                    sidx = int(student_ans)
                except Exception:
                    return 0, pts
                return (pts if sidx in correct else 0), pts

            if qtype == "true-false":
                ca = _norm(q.get("correctAnswer")).lower()
                sa = _norm(student_ans).lower()
                if ca in ("true", "false") and sa in ("true", "false") and sa == ca:
                    return pts, pts
                return 0, pts

            if qtype == "matching":
                correct = q.get("correctAnswers") or []
                if not isinstance(correct, list):
                    correct = []
                left_items = q.get("leftItems") or []
                if not isinstance(left_items, list):
                    left_items = []

                mapping = {}
                if isinstance(student_ans, dict):
                    for k, v in student_ans.items():
                        try:
                            ik = int(k)
                        except Exception:
                            continue
                        if v is None or v == "":
                            mapping[ik] = None
                        else:
                            try:
                                mapping[ik] = int(v)
                            except Exception:
                                mapping[ik] = None

                n = min(len(left_items), len(correct))
                if n <= 0:
                    return 0, pts

                for i in range(n):
                    if mapping.get(i, None) != correct[i]:
                        return 0, pts
                return pts, pts

            if qtype == "numerical":
                try:
                    ca = float(q.get("correctAnswer"))
                    sa = float(student_ans)
                except Exception:
                    return 0, pts
                tol = q.get("tolerance")
                try:
                    tol = float(tol) if tol is not None else 0.0
                except Exception:
                    tol = 0.0
                if abs(sa - ca) <= tol:
                    return pts, pts
                return 0, pts

            if qtype == "identification":
                ca = _norm(q.get("correctAnswer"))
                sa = _norm(student_ans)
                case_ins = q.get("caseInsensitive")
                if case_ins is None:
                    case_ins = True
                if bool(case_ins):
                    if sa.lower() == ca.lower() and ca != "":
                        return pts, pts
                else:
                    if sa == ca and ca != "":
                        return pts, pts
                return 0, pts

            if qtype == "image-answer":
                # Image answers are marked as pending for manual grading
                # Return 0 points now, but instructor will grade later
                return 0, pts

            return 0, pts

        score = 0
        total_points = 0
        for q in quiz_questions:
            qid = q.get("id")
            student_ans = get_answer_for(qid)
            earned, possible = grade_one(q, student_ans)
            score += int(earned)
            total_points += int(possible)

    # ========================================
    # STEP 6: PERSIST SUBMISSION
    # Save graded attempt to database
    # ========================================
    answers_hash = pg_compute_answers_hash(answers)
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            # NEW: Check if attempt_id exists (for multiple attempts support)
            cur.execute(
                "SELECT 1 FROM quiz_attempts WHERE attempt_id = %s LIMIT 1;",
                (str(attempt_id),),
            )
            attempt_exists = cur.fetchone() is not None
            
            if attempt_exists:
                # Update existing attempt
                cur.execute(
                    """
                    UPDATE quiz_attempts
                    SET quiz_title = %s,
                        score = %s,
                        total_points = %s,
                        submitted_at = now(),
                        submitted_ip = %s,
                        submission_token_used = TRUE,
                        answers_json = %s,
                        correct_answers_hash = %s
                    WHERE attempt_id = %s;
                    """,
                    (
                        quiz_title,
                        int(score),
                        int(total_points),
                        client_ip,
                        psycopg2.extras.Json(answers),
                        answers_hash,
                        str(attempt_id),
                    ),
                )
            else:
                # Insert new attempt
                cur.execute(
                    """
                    INSERT INTO quiz_attempts
                      (attempt_id, user_id, quiz_id, quiz_title, score, total_points, 
                       submitted_at, submitted_ip, submission_token_used, answers_json, 
                       correct_answers_hash)
                    VALUES (%s, %s, %s, %s, %s, %s, now(), %s, TRUE, %s, %s);
                    """,
                    (
                        str(attempt_id),
                        user_id,
                        quiz_id,
                        quiz_title,
                        int(score),
                        int(total_points),
                        client_ip,
                        psycopg2.extras.Json(answers),
                        answers_hash,
                    ),
                )
            conn.commit()
    except Exception as e:
        app.logger.error(f"Quiz submission failed: {type(e).__name__}: {str(e)}")
        return fail("Operation failed", 500)

    # ========================================
    # STEP 7: AUDIT LOGGING
    # Log submission attempt with full details
    # ========================================
    pg_log_submission_attempt(
        attempt_id, user_id, quiz_id, submission_token, True, client_ip,
        user_agent, answers_hash, int(score), int(total_points),
        tamper_detected=False, tamper_reason=""
    )

    return ok(
        {
            "attempt_id": str(attempt_id),
            "quiz_id": quiz_id,
            "score": int(score),
            "total_points": int(total_points),
            "saved": True,
        },
        "Quiz submitted securely",
    )

@app.route("/api/quiz-attempts/<attempt_id>/image-answers", methods=["GET"])
def api_get_image_answers(attempt_id):
    """
    Get all image answers from a quiz attempt that require manual grading.
    Only the instructor who created the quiz can access this.
    """
    guard = instructor_required()
    if guard:
        return fail("Not logged in", 401)

    try:
        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get the quiz attempt
            cur.execute(
                """
                SELECT attempt_id, quiz_id, user_id, submitted_at, answers_json
                FROM quiz_attempts
                WHERE attempt_id = %s;
                """,
                (str(attempt_id),),
            )
            attempt = cur.fetchone()
            if not attempt:
                return fail("Attempt not found", 404)

            # Get the quiz to verify instructor access and get question details
            cur.execute(
                """
                SELECT id, created_by, questions_json
                FROM quizzes
                WHERE id = %s;
                """,
                (attempt["quiz_id"],),
            )
            quiz = cur.fetchone()
            if not quiz:
                return fail("Quiz not found", 404)

            # Check if logged-in instructor created this quiz
            if str(quiz["created_by"]) != str(session.get("user_id")):
                return fail("Not authorized to grade this quiz", 403)

            answers = attempt.get("answers_json") or {}
            questions_data = quiz.get("questions_json") or {}
            questions = questions_data.get("questions") or []

            # Extract image answers
            image_answers = []
            for q in questions:
                if q.get("type") == "image-answer":
                    qid = q.get("id")
                    student_ans = answers.get(str(qid)) or answers.get(qid)

                    if isinstance(student_ans, dict) and student_ans.get("data"):
                        image_answers.append({
                            "question_id": qid,
                            "question_text": q.get("text"),
                            "points": q.get("points"),
                            "file_name": student_ans.get("name", "image"),
                            "image_data": student_ans.get("data"),
                            "submitted_at": attempt.get("submitted_at"),
                        })

            return ok({
                "attempt_id": str(attempt_id),
                "quiz_id": attempt["quiz_id"],
                "student_id": attempt["user_id"],
                "image_answers": image_answers,
            }, "Image answers retrieved")

    except Exception as e:
        return fail(f"Error: {str(e)}", 500)

@app.route("/api/quiz-attempts/<attempt_id>/image-answers/<question_id>/grade", methods=["POST"])
def api_grade_image_answer(attempt_id, question_id):
    """
    Grade an image answer question manually.
    Updates the quiz attempt with the score for this question.
    """
    guard = instructor_required()
    if guard:
        return fail("Not logged in", 401)
    if not _require_csrf_json():
        return fail("CSRF failed", 400)

    data = request.get_json(silent=True) or {}
    score = _safe_int(data.get("score"), 0)
    feedback = str(data.get("feedback") or "").strip()

    try:
        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get the attempt
            cur.execute(
                """
                SELECT attempt_id, quiz_id, answers_json
                FROM quiz_attempts
                WHERE attempt_id = %s;
                """,
                (str(attempt_id),),
            )
            attempt = cur.fetchone()
            if not attempt:
                return fail("Attempt not found", 404)

            # Get quiz to verify instructor access and get question points
            cur.execute(
                """
                SELECT id, created_by, questions_json
                FROM quizzes
                WHERE id = %s;
                """,
                (attempt["quiz_id"],),
            )
            quiz = cur.fetchone()
            if not quiz:
                return fail("Quiz not found", 404)

            if str(quiz["created_by"]) != str(session.get("user_id")):
                return fail("Not authorized to grade this quiz", 403)

            # Find the question to verify it's an image-answer type and get max points
            questions = (quiz.get("questions_json") or {}).get("questions") or []
            question = None
            for q in questions:
                if str(q.get("id")) == str(question_id):
                    question = q
                    break

            if not question:
                return fail("Question not found", 404)

            if question.get("type") != "image-answer":
                return fail("This is not an image-answer question", 400)

            max_points = _safe_int(question.get("points"), 0)
            if score < 0 or score > max_points:
                return fail(f"Score must be between 0 and {max_points}", 400)

            # Update the answer with grading info
            answers = attempt.get("answers_json") or {}
            qid_str = str(question_id)

            if qid_str in answers:
                ans = answers[qid_str]
                if isinstance(ans, dict):
                    ans["graded_score"] = score
                    ans["feedback"] = feedback
                    ans["graded_at"] = datetime.now().isoformat()
                    ans["graded_by"] = str(session.get("user_id"))

            # Recalculate total score
            total_score = 0
            total_points = 0
            for q in questions:
                qid = str(q.get("id"))
                pts = _safe_int(q.get("points"), 0)
                total_points += pts

                if qid in answers:
                    ans = answers[qid]
                    if isinstance(ans, dict):
                        if q.get("type") == "image-answer":
                            total_score += ans.get("graded_score", 0)
                        else:
                            # For non-image questions, assume they were graded immediately
                            pass

            # Update the attempt
            cur.execute(
                """
                UPDATE quiz_attempts
                SET answers_json = %s,
                    score = %s
                WHERE attempt_id = %s;
                """,
                (
                    psycopg2.extras.Json(answers),
                    int(total_score),
                    str(attempt_id),
                ),
            )
            conn.commit()

            return ok({
                "attempt_id": str(attempt_id),
                "question_id": question_id,
                "score": score,
                "feedback": feedback,
                "total_score": total_score,
            }, "Image answer graded successfully")

    except Exception as e:
        return fail(f"Error: {str(e)}", 500)

@app.route("/api/quiz-attempts/<attempt_id>/face-check", methods=["POST"])
def api_quiz_face_check(attempt_id):
    """
    CHANGED: Continuous face monitoring endpoint called periodically during
    the quiz session. Receives a 128D embedding captured from the student's
    webcam, compares it against the logged-in user's stored embeddings in
    Firebase, and returns whether the face matches.
    If a mismatch is detected, a violation is automatically logged to the DB.
    The quiz frontend uses the response to pause/resume the quiz.

    CHANGED: Also handles face_count > 1 (multiple persons) sent from the
    frontend. When multiple faces are detected the endpoint logs a
    "multiple_faces_detected" violation and returns status="multiple_faces"
    so the frontend knows to show the dedicated dialog.

    CHANGED: All violation INSERTs now use the correct 5-column schema:
      (id, attempt_id, violation_type, timestamp_iso, time_remaining)
    user_id is NOT stored here — it is resolved via quiz_attempts JOIN.

    CHANGED: Uses the SAME calibrated 85% confidence logic as quiz_capture
    so the initial verification and continuous monitoring remain consistent.

    CHANGED: Emits WebSocket events to:
      - student room   -> blackout_on / blackout_off
      - instructor room -> violation_alert

    Expected JSON body:
        { "embedding": [128 floats] | null, "face_count": int, "quizId": "..." }

    Returns:
        { status: "match" | "mismatch" | "no_face" | "multiple_faces" | "no_biometrics",
          confidence: float,
          confidence_percent: float,
          face_count: int }
    """
    guard = student_required()
    if guard:
        return fail("Not logged in", 401)

    if not _require_csrf_json():
        return fail("CSRF failed", 400)

    # CHANGED: Ensure violation table exists with correct schema before any insert
    # CHANGED: violation table schema is verified once at app startup; avoid repeated checks here.

    data = request.get_json(silent=True) or {}
    embedding = data.get("embedding")
    face_count = int(data.get("face_count") or 0)

    class_id = str(session.get("active_class_id") or "")
    quiz_id = str(data.get("quizId") or "")
    if not quiz_id:  # CHANGED
        quiz_id = str(data.get("quiz_id") or "")

    # CHANGED: Shared helper to insert violations using the correct schema
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
                        str(attempt_id),
                        vtype,
                        datetime.utcnow().isoformat() + "Z",
                        data.get("timeRemaining"),
                    ),
                )
                conn.commit()
                print(f"✅ Violation logged: {vtype} for attempt {attempt_id}", flush=True)
        except Exception as e:
            print(f"❌ Violation insert failed [{vtype}]: {str(e)}", flush=True)

    # CHANGED: Handle multiple faces
    if face_count > 1:
        _log_violation("multiple_faces_detected")

        ws_payload = {  # CHANGED
            "attempt_id": str(attempt_id),  # CHANGED
            "class_id": class_id,  # CHANGED
            "quiz_id": quiz_id,  # CHANGED
            "event_type": "blackout_on",  # CHANGED
            "violation_type": "multiple_faces_detected",  # CHANGED
            "timestamp": datetime.utcnow().isoformat() + "Z",  # CHANGED
            "face_count": face_count,  # CHANGED
        }  # CHANGED

        _emit_student_blackout_on(str(attempt_id), ws_payload)
        if class_id and quiz_id:  # CHANGED
            _emit_instructor_violation_alert(class_id, quiz_id, ws_payload)

        return ok(
            {
                "status": "multiple_faces",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
            },
            "Multiple faces detected"
        )

    # CHANGED: Graceful no-face handling. Do not log warning and pause at the same time.
    if embedding is None or embedding == "no_face":
        attempt_key = str(attempt_id)
        ATTEMPT_NO_FACE_COUNT[attempt_key] = ATTEMPT_NO_FACE_COUNT.get(attempt_key, 0) + 1
        ATTEMPT_MISMATCH_COUNT[attempt_key] = 0
        ATTEMPT_MULTI_FACE_COUNT[attempt_key] = 0

        current_count = ATTEMPT_NO_FACE_COUNT[attempt_key]
        print(f"⚠️ REST no-face count {current_count}/{NO_FACE_PAUSE_COUNT} for attempt {attempt_key}", flush=True)

        if current_count < NO_FACE_WARNING_COUNT:
            return ok({"status": "monitoring_tolerated", "reason": "temporary_no_face", "confidence": 0.0, "confidence_percent": 0.0, "face_count": face_count, "count": current_count, "action": "tolerated"}, "No face detected - within grace period")

        if current_count == NO_FACE_WARNING_COUNT:
            # CHANGED: No warning/logging for temporary no-face because looking down to write is normal.
            return ok({"status": "monitoring_tolerated", "reason": "temporary_no_face", "confidence": 0.0, "confidence_percent": 0.0, "face_count": face_count, "count": current_count, "action": "tolerated"}, "Temporary no-face tolerated")

        if current_count >= NO_FACE_PAUSE_COUNT:
            _log_violation("no_face_pause")
            ATTEMPT_BLACKOUT_STATE[attempt_key] = True
            ATTEMPT_NO_FACE_COUNT[attempt_key] = 0
            ws_payload = {"attempt_id": attempt_key, "class_id": class_id, "quiz_id": quiz_id, "event_type": "blackout_on", "violation_type": "no_face_pause", "timestamp": datetime.utcnow().isoformat() + "Z", "face_count": face_count}
            _emit_student_blackout_on(attempt_key, ws_payload)
            if class_id and quiz_id:
                _emit_instructor_violation_alert(class_id, quiz_id, ws_payload)
            return ok({"status": "no_face", "confidence": 0.0, "confidence_percent": 0.0, "face_count": face_count, "count": current_count, "action": "blackout_on"}, "No face detected - quiz paused")

        return ok({"status": "monitoring_tolerated", "reason": "temporary_no_face", "confidence": 0.0, "confidence_percent": 0.0, "face_count": face_count, "count": current_count, "action": "tolerated"}, "No face detected - waiting before pause")

    if not isinstance(embedding, list) or len(embedding) != 128:
        return fail("Invalid embedding format", 400)

    firebase_uid = str(session.get("firebase_uid") or "")
    if not firebase_uid:
        return fail("Missing Firebase UID in session", 401)

    # CHANGED: Retrieve all stored embeddings for this user from Firebase
    enc_list = fb_get_embedding_enc(firebase_uid)
    if not enc_list:
        return ok(
            {
                "status": "no_biometrics",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
            },
            "No biometrics registered"
        )

    # CHANGED: Decrypt all stored embeddings
    stored_embs = []
    for enc in enc_list:
        try:
            emb = decrypt_embedding(enc)
            if isinstance(emb, list) and len(emb) == 128:
                stored_embs.append(emb)
        except Exception:
            continue

    if not stored_embs:
        return ok(
            {
                "status": "no_biometrics",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
            },
            "No valid biometrics"
        )

    # CHANGED: Compare live embedding against all stored embeddings, take best match
    best_distance = _best_distance_against_embeddings(embedding, stored_embs)

    # CHANGED: Use the SAME calibrated confidence logic as quiz_capture
    MAX_DISTANCE = 2.10  # CHANGED
    confidence = max(0.0, 1.0 - (best_distance / MAX_DISTANCE)) if best_distance < 999.0 else 0.0  # CHANGED

    # CHANGED: Keep 85% confidence threshold consistent with quiz_capture
    CONFIDENCE_THRESHOLD = 0.85  # CHANGED
    matched = confidence >= CONFIDENCE_THRESHOLD  # CHANGED

    print(
        f"🔍 Face check: distance={best_distance:.4f}, confidence={confidence:.2%}, "
        f"matched={matched}, user={session.get('user_id')}",
        flush=True
    )

   # CHANGED: If mismatch, log violation and emit blackout
    if not matched:
        _log_violation("face_mismatch")

        ws_payload = {  # CHANGED
            "attempt_id": str(attempt_id),  # CHANGED
            "class_id": class_id,  # CHANGED
            "quiz_id": quiz_id,  # CHANGED
            "event_type": "blackout_on",  # CHANGED
            "violation_type": "face_mismatch",  # CHANGED
            "timestamp": datetime.utcnow().isoformat() + "Z",  # CHANGED
            "face_count": face_count,  # CHANGED
            "confidence": round(float(confidence), 4),  # CHANGED
            "confidence_percent": round(float(confidence) * 100, 2),  # CHANGED
        }  # CHANGED

        print(f"🚨 Emitting student blackout_on: {ws_payload}", flush=True)
        _emit_student_blackout_on(str(attempt_id), ws_payload)

        if class_id and quiz_id:  # CHANGED
            print(f"🚨 Emitting instructor violation_alert: room=class_{class_id}_quiz_{quiz_id}", flush=True)
            _emit_instructor_violation_alert(class_id, quiz_id, ws_payload)
        else:  # CHANGED
            print(f"⚠️ Skipped instructor emit because class_id or quiz_id missing. class_id={class_id}, quiz_id={quiz_id}", flush=True)

    # CHANGED: If matched, emit blackout_off so student can resume cleanly
    else:
        # CHANGED: Optional tolerance-based motion monitoring.
        # This does not change face matching, distance, cosine, or 85% confidence logic.
        motion_event = detect_tolerant_motion_event(str(attempt_id), data)
        if motion_event:
            _log_violation(motion_event["violation_type"])
            motion_payload = {
                "attempt_id": str(attempt_id),
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
                _emit_student_warning(str(attempt_id), motion_payload)
            else:
                ATTEMPT_BLACKOUT_STATE[str(attempt_id)] = True
                _emit_student_blackout_on(str(attempt_id), motion_payload)
            if class_id and quiz_id:
                _emit_instructor_violation_alert(class_id, quiz_id, motion_payload)
            if motion_event["action"] == "blackout_on":
                return ok(
                    {
                        "status": "motion_violation",
                        "confidence": round(float(confidence), 4),
                        "confidence_percent": round(float(confidence) * 100, 2),
                        "face_count": face_count,
                        "action": "blackout_on",
                        "violation_type": motion_event["violation_type"],
                    },
                    "Motion violation detected - quiz paused"
                )
        ws_payload = {  # CHANGED
            "attempt_id": str(attempt_id),  # CHANGED
            "class_id": class_id,  # CHANGED
            "quiz_id": quiz_id,  # CHANGED
            "event_type": "blackout_off",  # CHANGED
            "violation_type": "face_match",  # CHANGED
            "timestamp": datetime.utcnow().isoformat() + "Z",  # CHANGED
            "face_count": face_count,  # CHANGED
            "confidence": round(float(confidence), 4),  # CHANGED
            "confidence_percent": round(float(confidence) * 100, 2),  # CHANGED
        }  # CHANGED

        print(f"✅ Emitting student blackout_off: {ws_payload}", flush=True)
        _emit_student_blackout_off(str(attempt_id), ws_payload)

    status = "match" if matched else "mismatch"
    return ok(
        {
            "status": status,
            "confidence": round(float(confidence), 4),
            "confidence_percent": round(float(confidence) * 100, 2),
            "face_count": face_count,
        },
        "Face check complete"
    )

@app.route("/get_student_answer/<attempt_id>", methods=["GET"])
def get_student_answer(attempt_id):
    # Check if user is logged in
    if not session.get("logged_in"):
        return fail("Authentication required", 401)

    try:
        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get the quiz attempt details
            cur.execute(
                """
                SELECT qa.attempt_id, qa.answers_json, qa.user_id, q.questions_json, u.full_name
                FROM quiz_attempts qa
                JOIN quizzes q ON q.id = qa.quiz_id::uuid
                JOIN users u ON u.id = qa.user_id
                WHERE qa.attempt_id = %s::uuid;
                """,
                (str(attempt_id),)
            )
            result = cur.fetchone()
            
            if not result:
                return fail("Attempt not found", 404)
            
            # Check authorization: instructor can view all, student can only view their own
            user_id = session.get("user_id")
            attempt_user_id = result.get("user_id")
            is_instructor = session.get("role") == "instructor"
            
            if not is_instructor and str(user_id) != str(attempt_user_id):
                return fail("You don't have permission to view this attempt", 403)
            
            answers_json = result.get("answers_json") or {}
            questions_json = result.get("questions_json") or {}
            student_name = result.get("full_name") or "Unknown"
            
            # Parse the JSON data
            if isinstance(answers_json, str):
                answers_json = json.loads(answers_json)
            if isinstance(questions_json, str):
                questions_json = json.loads(questions_json)
            
            # Build the answers array with questions and student responses
            answers_array = []
            questions_list = questions_json.get("questions", []) if isinstance(questions_json, dict) else questions_json
            
            for i, question in enumerate(questions_list):
                q_id = question.get("id") or str(i)
                student_answer = answers_json.get(str(q_id)) or answers_json.get(q_id) or ""
                
                answer_obj = {
                    "question_id": str(q_id),
                    "question": question.get("question_text") or question.get("text") or f"Question {i+1}",
                    "question_type": question.get("type") or "text",
                    "student_answer": str(student_answer),
                    "correct_answer": question.get("correct_answer") or question.get("answer") or "",
                    "points": question.get("points") or 0,
                    "student_score": answers_json.get(f"{q_id}_score") or 0
                }
                answers_array.append(answer_obj)
            
            return ok({
                "student_name": student_name,
                "answers": answers_array
            }, "Student answers retrieved", 200)
            
    except Exception as e:
        app.logger.error(f"Error retrieving student answer: {type(e).__name__}: {str(e)}", exc_info=True)
        return fail("Operation failed", 500)
def update_grade(attempt_id):
    # Check instructor auth without redirecting
    if not session.get("logged_in") or session.get("role") != "instructor":
        return fail("Instructor access required", 401)

    try:
        data = request.get_json() or {}
        new_score = data.get("new_score")
        reason = data.get("reason", "")
        question_scores = data.get("question_scores", {})
        
        if new_score is None:
            return fail("New score is required", 400)
        
        new_score = float(new_score)
        
        if new_score < 0:
            return fail("Score cannot be negative", 400)
        
        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get the current attempt to verify it exists and get total points
            cur.execute(
                """
                SELECT qa.attempt_id, qa.total_points, qa.answers_json, qa.score as old_score
                FROM quiz_attempts qa
                WHERE qa.attempt_id = %s::uuid;
                """,
                (str(attempt_id),)
            )
            result = cur.fetchone()
            
            if not result:
                return fail("Attempt not found", 404)
            
            total_points = result.get("total_points") or 0
            old_score = result.get("old_score") or 0
            answers_json = result.get("answers_json") or {}
            
            if new_score > total_points:
                return fail(f"Score cannot exceed total points ({total_points})", 400)
            
            # Update answers_json with new question scores if provided
            if isinstance(answers_json, str):
                answers_json = json.loads(answers_json)
            
            # Update individual question scores
            if question_scores:
                for q_id, score in question_scores.items():
                    answers_json[f"{q_id}_score"] = float(score)
            
            # Update the quiz attempt with new score
            cur.execute(
                """
                UPDATE quiz_attempts
                SET score = %s, answers_json = %s
                WHERE attempt_id = %s::uuid
                RETURNING attempt_id;
                """,
                (new_score, json.dumps(answers_json), str(attempt_id))
            )
            
            conn.commit()
            
            return ok({
                "attempt_id": str(attempt_id),
                "old_score": old_score,
                "new_score": new_score,
                "reason": reason
            }, "Grade updated successfully", 200)
            
    except ValueError as e:
        return fail(f"Invalid score value: {str(e)}", 400)
    except Exception as e:
        print(f"Error in update_grade: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        return fail(f"Error updating grade: {str(e)}", 500)
