"""Student quiz verification, attempt lifecycle, proctoring, and grading routes."""

from services.response_service import ok

from ._shared import _set_liveness_running, load_app_context


load_app_context(globals())


# Quiz verification has its own stricter access boundary.
# Do not reuse FACE_VERIFY_ACCEPT_DISTANCE here because that value is also
# used by continuous monitoring, which intentionally needs more tolerance.
QUIZ_FACE_CONFIDENCE_THRESHOLD = float(globals().get("QUIZ_FACE_CONFIDENCE_THRESHOLD", FACE_VERIFY_CONFIDENCE_THRESHOLD))
QUIZ_FACE_ACCEPT_DISTANCE = float(globals().get("QUIZ_FACE_ACCEPT_DISTANCE", 0.17))
QUIZ_FACE_HARD_MAX_DISTANCE = float(globals().get("QUIZ_FACE_HARD_MAX_DISTANCE", QUIZ_FACE_ACCEPT_DISTANCE))
QUIZ_FACE_REJECT_DISTANCE = float(globals().get("QUIZ_FACE_REJECT_DISTANCE", FACE_VERIFY_REJECT_DISTANCE))
QUIZ_VERIFY_STRICT_FRONT_REQUIRED_MATCH_COUNT = int(globals().get("QUIZ_VERIFY_STRICT_FRONT_REQUIRED_MATCH_COUNT", FACE_VERIFY_REGISTERED_MIN_MATCH_COUNT))



def _normalise_quiz_attempt_id(value):
    """
    CHANGED:
    Normalise attempt IDs received from browser routes/events.
    This prevents placeholder values such as "undefined" or "null" from
    being used for monitoring, draft, or violation handling.
    """
    attempt_key = str(value or "").strip()
    if attempt_key.lower() in ("", "none", "null", "undefined"):
        return ""
    return attempt_key


def _quiz_attempt_is_ready(attempt_id, user_id=None, quiz_id=None, require_open=True):
    """
    CHANGED:
    Backend-side attempt readiness check.

    Monitoring and violation logging should only proceed after the quiz
    attempt exists in PostgreSQL. This avoids ForeignKeyViolation errors when
    browser events arrive before the attempt row is ready.

    This does NOT change face distance, thresholds, environment variables, or
    deployment settings.
    """
    attempt_key = _normalise_quiz_attempt_id(attempt_id)
    if not attempt_key:
        return False, "missing_attempt_id"

    try:
        conditions = ["attempt_id = %s"]
        params = [attempt_key]

        if user_id:
            conditions.append("user_id = %s")
            params.append(str(user_id))

        if quiz_id:
            conditions.append("quiz_id = %s")
            params.append(str(quiz_id))

        if require_open:
            conditions.append("submitted_at IS NULL")

        sql = f"""
            SELECT 1
            FROM quiz_attempts
            WHERE {' AND '.join(conditions)}
            LIMIT 1;
        """

        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return (cur.fetchone() is not None), "ready"

    except Exception as err:
        app.logger.warning(
            "Quiz attempt readiness check failed: %s: %s",
            type(err).__name__,
            str(err),
        )
        return False, "attempt_check_failed"


def _quiz_attempt_not_ready_payload(attempt_id, reason):
    """
    CHANGED:
    Standard response for skipped monitoring/violation requests.
    """
    return {
        "saved": False,
        "status": "attempt_not_ready",
        "reason": reason or "attempt_not_ready",
        "attempt_id": _normalise_quiz_attempt_id(attempt_id),
        "action": "skipped",
    }


def _calibrated_quiz_face_confidence(best_distance):
    """
    Map quiz-entry embedding distance to a user-facing confidence score.

    Quiz entry intentionally uses QUIZ_FACE_ACCEPT_DISTANCE instead of the
    global FACE_VERIFY_ACCEPT_DISTANCE. This keeps the quiz access gate strict
    while allowing continuous monitoring to remain more tolerant.
    """
    try:
        distance = float(best_distance)
    except Exception:
        return 0.0

    if distance >= 999.0:
        return 0.0

    if distance <= QUIZ_FACE_ACCEPT_DISTANCE:
        headroom = max(0.01, QUIZ_FACE_ACCEPT_DISTANCE)
        bonus = (QUIZ_FACE_ACCEPT_DISTANCE - max(0.0, distance)) / headroom
        return min(0.99, QUIZ_FACE_CONFIDENCE_THRESHOLD + (bonus * 0.14))

    reject_span = max(0.01, QUIZ_FACE_REJECT_DISTANCE - QUIZ_FACE_ACCEPT_DISTANCE)
    overage = min(1.0, (distance - QUIZ_FACE_ACCEPT_DISTANCE) / reject_span)
    return max(0.0, QUIZ_FACE_CONFIDENCE_THRESHOLD * (1.0 - overage))


def _quiz_face_match_passes(best_distance):
    """
    Strict pass/fail helper for quiz entry and quiz re-verification.

    A quiz-entry frame passes only when it reaches the 85% confidence boundary
    under the stricter quiz distance and is still inside the quiz hard maximum.
    """
    confidence = _calibrated_quiz_face_confidence(best_distance)

    try:
        distance = float(best_distance)
    except Exception:
        return False, confidence

    matched = (
        distance <= QUIZ_FACE_HARD_MAX_DISTANCE
        and confidence >= QUIZ_FACE_CONFIDENCE_THRESHOLD
    )

    return bool(matched), float(confidence)

def _decode_quiz_browser_frame_data(frame_data):
    """
    CHANGED:
    Decode the clean front-facing frame submitted by camera.html.

    Liveness is still validated using liveness_sequence first. This frame is
    used only after liveness passes, so quiz verification can compare the
    clean selected/front frame rather than an arbitrary returned sequence frame.
    """
    if not frame_data:
        return None, "No submitted frame data."

    try:
        import base64

        raw = str(frame_data or "")
        if "," in raw:
            raw = raw.split(",", 1)[1]

        frame_bytes = base64.b64decode(raw)
        file_bytes = np.frombuffer(frame_bytes, dtype=np.uint8)
        frame = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

        if frame is None:
            return None, "Could not decode submitted frame."

        return frame, None
    except Exception as err:
        app.logger.warning("Quiz submitted frame decode failed: %s", type(err).__name__)
        return None, "Could not read submitted frame."



def _quiz_best_match_summary(live_emb, stored_embs):
    """
    CHANGED:
    Simple IT-style face verification summary.

    The submitted live embedding is compared with the stored embeddings and the
    closest / lowest distance is used for the decision. This removes the
    multi-sample agreement requirement while keeping the existing 85% confidence and
    strict quiz distance boundary through _quiz_face_match_passes().
    """
    distances = []

    for stored in stored_embs or []:
        try:
            distance_helper = globals().get("_face_distance")
            if callable(distance_helper):
                dist = distance_helper(live_emb, stored)
            else:
                live_arr = np.asarray(live_emb, dtype=np.float32).reshape(-1)
                stored_arr = np.asarray(stored, dtype=np.float32).reshape(-1)
                if live_arr.size != 128 or stored_arr.size != 128:
                    continue
                dist = float(np.linalg.norm(live_arr - stored_arr))

            if float(dist) < 999.0:
                distances.append(float(dist))
        except Exception:
            continue

    distances.sort()
    best_distance = distances[0] if distances else 999.0
    matched, confidence = _quiz_face_match_passes(best_distance)

    return {
        "matched": bool(matched),
        "confidence": float(confidence),
        "best_distance": float(best_distance),
        "total_embeddings": int(len(distances)),
        # Kept for response compatibility only. This is no longer multi-sample agreement logic.
        "matched_count": 1 if matched else 0,
        "required_match_count": 1,
        "distance_debug": [
            (idx + 1, round(float(dist), 4))
            for idx, dist in enumerate(distances)
        ],
        "match_policy_mode": "best_match",
    }


def _quiz_strict_front_identity_summary(live_emb, stored_embs):
    """
    Strict quiz-entry identity policy.

    The submitted front frame is still the only live frame used for identity.
    However, access is no longer granted by one closest stored template only.
    The live frame must match several of the registered strict-front identity
    embeddings. This blocks cases where a wrong user is close to only part of
    the stored reference set.
    """
    distances = []

    for stored in stored_embs or []:
        try:
            distance_helper = globals().get("_face_distance")
            if callable(distance_helper):
                dist = distance_helper(live_emb, stored)
            else:
                live_arr = np.asarray(live_emb, dtype=np.float32).reshape(-1)
                stored_arr = np.asarray(stored, dtype=np.float32).reshape(-1)
                if live_arr.size != 128 or stored_arr.size != 128:
                    continue
                dist = float(np.linalg.norm(live_arr - stored_arr))

            if float(dist) < 999.0:
                distances.append(float(dist))
        except Exception:
            continue

    distances.sort()
    best_distance = distances[0] if distances else 999.0
    best_matched, confidence = _quiz_face_match_passes(best_distance)

    matched_distances = []
    for dist in distances:
        try:
            dist_matched, _ = _quiz_face_match_passes(float(dist))
            if dist_matched:
                matched_distances.append(float(dist))
        except Exception:
            continue

    required = int(globals().get(
        "QUIZ_VERIFY_STRICT_FRONT_REQUIRED_MATCH_COUNT",
        globals().get("FACE_VERIFY_REGISTERED_MIN_MATCH_COUNT", 4),
    ))
    required = max(4, required)

    matched_count = len(matched_distances)
    matched = bool(best_matched and matched_count >= required)

    return {
        "matched": bool(matched),
        "confidence": float(confidence),
        "best_distance": float(best_distance),
        "total_embeddings": int(len(distances)),
        "matched_count": int(matched_count),
        "required_match_count": int(required),
        "matched_distances": [round(float(dist), 4) for dist in matched_distances],
        "distance_debug": [
            (idx + 1, round(float(dist), 4))
            for idx, dist in enumerate(distances)
        ],
        "match_policy_mode": "strict_front_4_of_5",
    }



def _quiz_verify_frame_quality_error(frame, face_box=None):
    """
    CHANGED:
    Quality gate for initial quiz verification only.

    This does NOT change the face distance formula, the 85% threshold,
    or the best-match verification policy. It only prevents weak live frames
    from being compared against the registered embeddings.

    If the frame is blurry, too dark, too bright, low contrast, too small,
    too large, off-centre, or not front-facing enough, the user is asked
    to retry instead of being treated as a face mismatch.
    """
    if frame is None:
        return "Camera frame is missing. Please try again.", {}

    try:
        if face_box is None:
            faces_raw = detect_faces(frame)
            face_box, err = pick_single_face(faces_raw, frame)
            if err or face_box is None:
                return err or "No clear face detected. Please try again.", {}
    except Exception:
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

        min_blur = float(globals().get("QUIZ_VERIFY_MIN_BLUR_SCORE", 30.0))
        min_brightness = float(globals().get("QUIZ_VERIFY_MIN_BRIGHTNESS", 30.0))
        max_brightness = float(globals().get("QUIZ_VERIFY_MAX_BRIGHTNESS", 235.0))
        min_contrast = float(globals().get("QUIZ_VERIFY_MIN_CONTRAST", 12.0))
        min_face_area = float(globals().get("ENROLLMENT_MIN_FACE_AREA", 0.045))
        max_face_area = float(globals().get("ENROLLMENT_MAX_FACE_AREA", 0.65))
        center_tolerance = float(globals().get("QUIZ_VERIFY_CENTER_TOLERANCE", 0.20))
        max_yaw_delta = float(globals().get("QUIZ_VERIFY_MAX_YAW_DELTA", 0.18))

        yaw = None
        try:
            yaw = yaw_ratio_from_face(frame, face_box)
        except Exception:
            yaw = None

        metrics = {
            "blur": round(blur, 2),
            "brightness": round(brightness, 2),
            "contrast": round(contrast, 2),
            "face_ratio": round(face_ratio, 4),
            "center_offset_x": round(center_offset_x, 4),
            "center_offset_y": round(center_offset_y, 4),
            "yaw": round(float(yaw), 4) if yaw is not None else None,
        }

        if blur < min_blur:
            return "Image is blurry. Please hold still and try again.", metrics
        if brightness < min_brightness:
            return "Face is too dark. Please improve lighting and try again.", metrics
        if brightness > max_brightness:
            return "Face is too bright. Please reduce lighting and try again.", metrics
        if contrast < min_contrast:
            return "Face has low contrast. Please adjust lighting and try again.", metrics
        if face_ratio < min_face_area:
            return "Face is too small. Please move closer and try again.", metrics
        if face_ratio > max_face_area:
            return "Face is too close. Please move back slightly and try again.", metrics
        if center_offset_x > center_tolerance or center_offset_y > center_tolerance:
            return "Please centre your face in the camera frame and try again.", metrics
        if yaw is not None and abs(float(yaw)) > max_yaw_delta:
            return "Please look straight at the camera and try again.", metrics

        return None, metrics

    except Exception as err:
        app.logger.warning("Quiz verification quality check failed: %s", type(err).__name__)
        return None, {}


def _is_quiz_verified_for_session(quiz_id) -> bool:
    return (
        bool(session.get("quiz_verified"))
        and str(session.get("quiz_verified_quiz_id") or "") == str(quiz_id)
    )


def _mark_quiz_verified_for_session(quiz_id) -> None:
    session["quiz_verified"] = True
    session["quiz_verified_quiz_id"] = str(quiz_id)
    session["pending_quiz_id"] = str(quiz_id)
    session["verified_name"] = session.get("student_name", "")
    session.modified = True


def _clear_quiz_verified_for_session(quiz_id) -> None:
    if str(session.get("quiz_verified_quiz_id") or "") != str(quiz_id):
        return

    session["quiz_verified"] = False
    session["verified_name"] = ""
    session.pop("quiz_verified_quiz_id", None)
    session.pop("pending_quiz_id", None)
    session.modified = True


def _exam_entry_attempt_session_key(quiz_id) -> str:
    return f"exam_entry_verify_attempts:{str(quiz_id or '').strip()}"


def _next_exam_entry_attempt_count(quiz_id) -> int:
    key = _exam_entry_attempt_session_key(quiz_id)
    try:
        value = int(session.get(key) or 0) + 1
    except Exception:
        value = 1
    session[key] = value
    session.modified = True
    return value


def _current_exam_entry_attempt_count(quiz_id) -> int:
    try:
        return max(1, int(session.get(_exam_entry_attempt_session_key(quiz_id)) or 1))
    except Exception:
        return 1


def _reset_exam_entry_attempt_count(quiz_id) -> None:
    session.pop(_exam_entry_attempt_session_key(quiz_id), None)
    session.modified = True


def _log_exam_entry_from_quiz_context(student_ctx, qrow, class_id, attempts_count, status, message="") -> None:
    try:
        if not student_ctx or not qrow or not class_id:
            return
        pg_log_exam_entry_event(
            student_id=str(student_ctx.get("user_id") or ""),
            student_name=student_ctx.get("name") or session.get("student_name") or "Unknown Student",
            email=student_ctx.get("email") or session.get("email") or "",
            quiz_id=str(qrow.get("id") or ""),
            quiz_title=qrow.get("title") or "Quiz",
            class_id=str(class_id),
            attempts_before_entry=attempts_count,
            status=status,
            ip_address=pg_get_client_ip(),
            user_agent=request.headers.get("User-Agent", ""),
            message=message,
        )
    except Exception as err:
        app.logger.warning("Exam entry log skipped: %s", err)

def _quiz_current_student_context():
    """
    CHANGED:
    Strict quiz-entry ownership guard.

    The browser session must point to one completed ClassiFace student profile.
    This prevents a Firebase-only/unregistered account, a stale browser session,
    or a mismatched PostgreSQL/Firebase mapping from borrowing another user's
    stored face embeddings during quiz verification.
    """
    pg_user_id = str(session.get("user_id") or "").strip()
    firebase_uid = str(session.get("firebase_uid") or "").strip()
    role = (session.get("role") or "").strip().lower()

    if not pg_user_id or not firebase_uid:
        return None, "Login session is incomplete. Please log in again."

    if role != "student":
        return None, "Only students can verify for quizzes."

    try:
        user_row = pg_find_user_by_firebase_uid(firebase_uid)
    except Exception as err:
        app.logger.warning(
            "Quiz owner check failed while reading user profile: %s",
            type(err).__name__,
        )
        return None, "Could not confirm your account. Please log in again."

    if not user_row:
        app.logger.warning(
            "[QUIZ-VERIFY-OWNER] blocked reason=no_profile firebase_uid=%s session_user_id=%s",
            _mask_uid(firebase_uid),
            pg_user_id,
        )
        return None, "Account is not fully registered. Please complete registration first."

    db_user_id = str(user_row.get("id") or "").strip()
    db_role = (user_row.get("role") or "").strip().lower()

    if db_user_id != pg_user_id:
        app.logger.warning(
            "[QUIZ-VERIFY-OWNER] blocked reason=session_user_mismatch firebase_uid=%s session_user_id=%s db_user_id=%s",
            _mask_uid(firebase_uid),
            pg_user_id,
            db_user_id,
        )
        return None, "Account session mismatch. Please log out and log in again."

    if db_role != "student":
        app.logger.warning(
            "[QUIZ-VERIFY-OWNER] blocked reason=not_student firebase_uid=%s role=%s",
            _mask_uid(firebase_uid),
            db_role,
        )
        return None, "Only students can verify for quizzes."

    return {
        "user_id": db_user_id,
        "firebase_uid": firebase_uid,
        "role": db_role,
        "email": (user_row.get("email") or "").strip().lower(),
        "name": user_row.get("full_name") or session.get("student_name") or "",
    }, None


def _quiz_load_strict_front_identity_embeddings(firebase_uid):
    """
    CHANGED:
    Quiz entry must use only the strict front identity samples saved during
    registration. Monitoring samples are intentionally not used here.
    """
    enc_list = fb_get_embedding_enc(firebase_uid)
    if not enc_list:
        return [], "No approved face registration was found. Please register your face first."

    stored_embs = []
    invalid_count = 0

    for enc in enc_list:
        try:
            emb = decrypt_embedding(enc)
            if isinstance(emb, list) and len(emb) == 128:
                stored_embs.append(emb)
            else:
                invalid_count += 1
        except Exception:
            invalid_count += 1

    if len(stored_embs) < REGISTRATION_SAMPLE_COUNT:
        app.logger.warning(
            "[QUIZ-VERIFY-OWNER] blocked reason=incomplete_front_templates firebase_uid=%s valid=%s required=%s invalid=%s",
            _mask_uid(firebase_uid),
            len(stored_embs),
            REGISTRATION_SAMPLE_COUNT,
            invalid_count,
        )
        return [], (
            f"Face registration is incomplete. You have {len(stored_embs)}/{REGISTRATION_SAMPLE_COUNT} "
            "approved identity samples. Please re-register."
        )

    # Keep exactly the strict approved front identity samples. This avoids any
    # accidental use of monitoring support samples for quiz-entry access.
    stored_embs = stored_embs[:REGISTRATION_SAMPLE_COUNT]

    app.logger.info(
        "[QUIZ-VERIFY-OWNER] strict_front_templates_loaded firebase_uid=%s count=%s",
        _mask_uid(firebase_uid),
        len(stored_embs),
    )
    return stored_embs, None


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
    session.pop("quiz_verified_quiz_id", None)
    session["verified_name"] = ""

    return redirect(url_for("quiz_verify"))

@app.route("/quiz_verify")
def quiz_verify():
    guard = student_required()
    if guard:
        return guard
    if not session.get("active_class_id"):
        return redirect_with_msg("/class-lists", "Please select your class first.")
    pending_quiz_id = (session.get("pending_quiz_id") or "").strip()
    verified_quiz_id = (session.get("quiz_verified_quiz_id") or "").strip()
    if session.get("quiz_verified") and pending_quiz_id and verified_quiz_id == pending_quiz_id:
        return redirect(url_for("stud_quiz_session", quiz_id=pending_quiz_id))
    if session.get("quiz_verified") and verified_quiz_id and not pending_quiz_id:
        session["pending_quiz_id"] = verified_quiz_id
        session.modified = True
        return redirect(url_for("stud_quiz_session", quiz_id=verified_quiz_id))
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
    frame_data = request.form.get("frame_data") or ""
    liveness_sequence = request.form.get("liveness_sequence") or ""
    state = _ensure_liveness_state(stream_key) if frame_data else _reset_liveness_state(stream_key)

    # CHANGED: Keep quiz-verification retry messages on the camera page.
    # This makes face mismatch / unclear face messages visible to the student
    # instead of sending them back to a page that may not show the message.
    is_reverify_request = (request.form.get("reverify") or request.args.get("reverify") or "") == "1"
    retry_camera_url = "/camera?mode=quiz&reverify=1" if is_reverify_request else "/camera?mode=quiz"
    exam_entry_log_context = {
        "ready": False,
        "student_ctx": None,
        "qrow": None,
        "class_id": "",
        "attempts_count": 1,
    }

    def retry_verification(message: str):
        if frame_data and exam_entry_log_context.get("ready"):
            _log_exam_entry_from_quiz_context(
                exam_entry_log_context.get("student_ctx"),
                exam_entry_log_context.get("qrow"),
                exam_entry_log_context.get("class_id"),
                exam_entry_log_context.get("attempts_count") or 1,
                "Failed",
                message or "Verification failed",
            )
        return redirect_with_msg(retry_camera_url, message)

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

    quiz_id = (session.get("pending_quiz_id") or "").strip()

    if not quiz_id:
        return redirect_with_msg(_stud_home_url(), "Please select a quiz first.")

    exam_entry_attempts_count = _next_exam_entry_attempt_count(quiz_id) if frame_data else _current_exam_entry_attempt_count(quiz_id)
    exam_entry_log_context["attempts_count"] = exam_entry_attempts_count

    student_ctx, owner_error = _quiz_current_student_context()
    if owner_error:
        session["quiz_verified"] = False
        session.pop("quiz_verified_quiz_id", None)
        session.modified = True
        return redirect_with_msg("/login", owner_error)

    pg_user_id = str(student_ctx["user_id"])
    firebase_uid = str(student_ctx["firebase_uid"])

    qrow = pg_get_quiz_by_id(quiz_id)
    if not qrow or str(qrow.get("class_id") or "") != str(class_id):
        return redirect_with_msg(_stud_home_url(), "Quiz not found for this class.")

    exam_entry_log_context.update({
        "ready": True,
        "student_ctx": student_ctx,
        "qrow": qrow,
        "class_id": class_id,
        "attempts_count": exam_entry_attempts_count,
    })

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

    if frame_data:
        ok_live, frame, live_err = validate_browser_liveness_sequence(liveness_sequence, stream_key)
        if not ok_live or frame is None:
            state["live_instruction"] = "Verification blocked"
            state["live_subtext"] = live_err or "Liveness failed"
            return retry_verification(live_err or "Liveness failed. Please try again.")

        # CHANGED:
        # Browser quiz verification now uses a short set of approved front-facing
        # frames from the liveness/front phase instead of trusting only one
        # submitted frame. This keeps the 85% access-control policy unchanged,
        # but reduces false failures caused by one unlucky frame.
        submitted_frame, decode_err = _decode_quiz_browser_frame_data(frame_data)
        if submitted_frame is None:
            state["live_instruction"] = "Verification image not clear"
            state["live_subtext"] = decode_err or "Could not decode the verification frame."
            return retry_verification(
                decode_err or "Could not decode the verification frame. Please try again."
            )

        stored_embs, template_error = _quiz_load_strict_front_identity_embeddings(firebase_uid)
        if template_error:
            session["quiz_verified"] = False
            session.modified = True
            return redirect_with_msg("/quiz_verify", template_error)

        # CHANGED:
        # Quiz identity verification now uses ONLY the submitted front frame.
        # Liveness is still required and validated above, but liveness/front-sequence
        # frames are NOT used for the identity decision. This prevents a stale or
        # inconsistent liveness frame from overriding the actual submitted quiz frame.
        #
        # Decision flow:
        #   submitted_front_frame -> generate embedding -> compare with the
        #   registered student's strict front identity embeddings_enc_list ->
        #   use the closest/best stored embedding distance.
        print(
            "[QUIZ-VERIFY-FRAME] browser_verification_frame_count=1 "
            "source=submitted_front_frame_only",
            flush=True,
        )

        face_crop, face_box, crop_err = prepare_face_crop_from_frame(submitted_frame, pad_ratio=0.20)
        if crop_err:
            session["quiz_verified"] = False
            session.modified = True
            state["live_instruction"] = "Verification image not clear"
            state["live_subtext"] = crop_err
            print(
                f"[QUIZ-VERIFY-FRAME] source=submitted_front_frame rejected crop_err={crop_err}",
                flush=True,
            )
            return retry_verification(
                crop_err or "No usable submitted front verification frame was captured. Please try again."
            )

        quality_error, quality_metrics = _quiz_verify_frame_quality_error(submitted_frame, face_box)
        print(
            f"[QUIZ-VERIFY-QUALITY] verification_frame=1 "
            f"source=submitted_front_frame metrics={quality_metrics} "
            f"accepted={quality_error is None}",
            flush=True,
        )
        if quality_error:
            session["quiz_verified"] = False
            session.modified = True
            state["live_instruction"] = "Verification image not clear"
            state["live_subtext"] = quality_error
            return retry_verification(
                quality_error or "Verification image is not clear. Please look straight and try again."
            )

        emb, err = generate_embedding(face_crop)
        if err:
            session["quiz_verified"] = False
            session.modified = True
            state["live_instruction"] = "Verification image not clear"
            state["live_subtext"] = err
            print(
                f"[QUIZ-VERIFY-FRAME] source=submitted_front_frame rejected embedding_err={err}",
                flush=True,
            )
            return retry_verification(
                err or "Could not generate a usable face verification sample. Please try again."
            )

        emb_list = np.asarray(emb, dtype=np.float32).reshape(-1).tolist()
        if len(emb_list) != 128:
            session["quiz_verified"] = False
            session.modified = True
            state["live_instruction"] = "Verification image not clear"
            state["live_subtext"] = "Invalid face verification sample."
            print(
                "[QUIZ-VERIFY-FRAME] source=submitted_front_frame rejected invalid_embedding_length",
                flush=True,
            )
            return retry_verification(
                "Could not generate a valid face verification sample. Please try again."
            )

        # Strict multi-template check against the registered strict front identity embeddings.
        # This still uses only the submitted_front_frame as the live verification frame.
        match_info = _quiz_strict_front_identity_summary(emb_list, stored_embs)
        best_distance = float(match_info.get("best_distance") or 999.0)
        confidence = float(match_info.get("confidence") or 0.0)
        matched = bool(match_info.get("matched"))
        matched_count = int(match_info.get("matched_count") or (1 if matched else 0))
        required_match_count = int(match_info.get("required_match_count") or 1)
        distance_debug = match_info.get("distance_debug") or []

        print(
            f"[QUIZ-VERIFY-MATCH] verification_frame=1 "
            f"source=submitted_front_frame distance={best_distance:.4f} "
            f"confidence={confidence:.2%} matched={matched} "
            f"policy=strict_front_4_of_5 strict_front_embeddings={len(stored_embs or [])}",
            flush=True,
        )

        print(
            f"[QUIZ-VERIFY-GATE] policy=submitted_front_strict_4of5_to_strict_embeddings "
            f"submitted_matched={matched} "
            f"submitted_distance={best_distance:.4f} "
            f"submitted_confidence={confidence:.2%} "
            f"stored_embedding_count={len(stored_embs or [])} "
            f"final_matched={matched}",
            flush=True,
        )

        if not matched:
            session["quiz_verified"] = False
            session.modified = True
            print(
                "[QUIZ-VERIFY-GATE] blocked reason=submitted_front_strict_4of5_failed "
                f"submitted_distance={best_distance:.4f} "
                f"submitted_confidence={confidence:.2%} "
                f"stored_embedding_count={len(stored_embs or [])}",
                flush=True,
            )
            return retry_verification(
                f"Face does not match your registration ({confidence:.0%}/85%). Please look straight, keep the same lighting, and try again."
            )

        cv2.imwrite(os.path.join(RECOG_FOLDER, "recognized.png"), face_crop)

        print(
            f"   Browser quiz face distance: {best_distance:.4f}, "
            f"confidence: {confidence:.2%}, "
            f"required: {int(QUIZ_FACE_CONFIDENCE_THRESHOLD * 100)}%, "
            f"matched={matched}, policy=submitted_front_strict_4of5_to_strict_embeddings, "
            f"source=submitted_front_frame, "
            f"stored_embedding_count={len(stored_embs or [])}, "
            f"matched_count={matched_count}, "
            f"required_match_count={required_match_count}, "
            f"distances={distance_debug}",
            flush=True,
        )

        _log_exam_entry_from_quiz_context(
            student_ctx,
            qrow,
            class_id,
            exam_entry_attempts_count,
            "Verified",
            "Quiz entry verification passed",
        )
        _mark_quiz_verified_for_session(quiz_id)
        state["live_instruction"] = "Verification successful"
        state["live_subtext"] = "Opening quiz"

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
            att_msg = "Attendance will sync later"

        # CHANGED: Successful browser quiz face verification must leave camera flow.
        # Use a GET redirect instead of rendering the quiz from POST /quiz_capture.
        # The signed handoff token also prevents the next page from falling back to
        # /quiz_verify if the browser/session needs one clean redirect step.
        try:
            verify_token = create_quiz_verify_token(str(quiz_id), class_id)
            print(
                f"[QUIZ-VERIFY-REDIRECT] matched=True target=quiz_verified_handoff quiz_id={quiz_id}",
                flush=True,
            )
            return redirect(url_for(
                "quiz_verified_handoff",
                quiz_id=str(quiz_id),
                token=verify_token,
            ))
        except Exception as redirect_err:
            print(
                f"[QUIZ-VERIFY-REDIRECT] token handoff failed: {type(redirect_err).__name__}; fallback=stud_quiz_session",
                flush=True,
            )
            return redirect(url_for("stud_quiz_session", quiz_id=str(quiz_id)))

    stored_embs, template_error = _quiz_load_strict_front_identity_embeddings(firebase_uid)
    if template_error:
        session["quiz_verified"] = False
        session.modified = True
        return redirect_with_msg("/quiz_verify", template_error)

    cap = _init_camera()
    state["liveness_preview_frame"] = None  # CHANGED

    direction, blinks_required = _new_challenge()

    _set_liveness_running(True)
    try:
        state["live_instruction"] = "Starting identification..."  # CHANGED
        state["live_subtext"] = f"Blink {blinks_required} times + turn {_direction_prompt(direction)}"  # CHANGED
        _flush_camera(cap, n=10)
        ok_live, frame, reason = pass_liveness_from_camera(cap, direction, blinks_required, stream_key)
    finally:
        _set_liveness_running(False)
        state["liveness_preview_frame"] = None  # CHANGED

    if not ok_live or frame is None:
        state["live_instruction"] = "Verification failed"  # CHANGED
        state["live_subtext"] = reason or "Liveness failed"  # CHANGED
        _release_camera_if_idle(force=True)
        return retry_verification(f"Liveness failed: {reason}")

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
        return retry_verification(face_err or "No usable face detected.")

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
        return retry_verification("Invalid face crop. Please try again.")

    if face_crop.shape[0] < 40 or face_crop.shape[1] < 40:
        _release_camera_if_idle(force=True)
        return retry_verification("Face too small. Please move closer and try again.")

    # CHANGED: Do not compare weak quiz-verification frames.
    # Ask the student to retry instead of counting the frame as mismatch.
    quality_error, quality_metrics = _quiz_verify_frame_quality_error(frame, face_box)
    print(
        f"[QUIZ-VERIFY-QUALITY] server metrics={quality_metrics} "
        f"accepted={quality_error is None}",
        flush=True,
    )
    if quality_error:
        state["live_instruction"] = "Verification image not clear"
        state["live_subtext"] = quality_error
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/quiz_verify", quality_error)

    cv2.imwrite(os.path.join(RECOG_FOLDER, "recognized.png"), face_crop)

    emb, err = generate_embedding(face_crop)
    if err:
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/quiz_verify", err)

    emb_list = np.asarray(emb, dtype=np.float32).reshape(-1).tolist()
    if len(emb_list) != 128:
        _release_camera_if_idle(force=True)
        return redirect_with_msg("/quiz_verify", "Embedding error. Please try again.")

    match_info = _quiz_strict_front_identity_summary(emb_list, stored_embs)
    best_distance = match_info["best_distance"]
    matched = match_info["matched"]
    confidence = match_info["confidence"]
    matched_count = match_info["matched_count"]
    required_match_count = match_info["required_match_count"]
    distance_debug = match_info["distance_debug"]

    print(
        f"   Best face distance: {best_distance:.4f}, "
        f"confidence: {confidence:.2%}, "
        f"required: {int(QUIZ_FACE_CONFIDENCE_THRESHOLD * 100)}%, "
        f"matched={matched}, policy=strict_front_4_of_5, "
        f"matched_count={matched_count}, required_match_count={required_match_count}, "
        f"distances={distance_debug}",
        flush=True
    )

    if matched:
        # =========================
        # CHANGED: SUCCESS OVERLAY
        # =========================
        state["live_instruction"] = "Verification successful"  # CHANGED
        state["live_subtext"] = "Preparing your quiz"  # CHANGED
        # =========================

        _log_exam_entry_from_quiz_context(
            student_ctx,
            qrow,
            class_id,
            exam_entry_attempts_count,
            "Verified",
            "Quiz entry verification passed",
        )
        _mark_quiz_verified_for_session(quiz_id)

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
            app.logger.error(f"Attendance database error: {type(e).__name__}")
            att_msg = "Attendance will sync later"

        _release_camera_if_idle(force=True)
        # CHANGED: Successful server-camera quiz face verification must also
        # leave camera flow through a GET redirect.
        try:
            verify_token = create_quiz_verify_token(str(quiz_id), class_id)
            print(
                f"[QUIZ-VERIFY-REDIRECT] matched=True target=quiz_verified_handoff quiz_id={quiz_id}",
                flush=True,
            )
            return redirect(url_for(
                "quiz_verified_handoff",
                quiz_id=str(quiz_id),
                token=verify_token,
            ))
        except Exception as redirect_err:
            print(
                f"[QUIZ-VERIFY-REDIRECT] token handoff failed: {type(redirect_err).__name__}; fallback=stud_quiz_session",
                flush=True,
            )
            return redirect(url_for("stud_quiz_session", quiz_id=str(quiz_id)))

    session["quiz_verified"] = False
    _release_camera_if_idle(force=True)
    return retry_verification("❌ Face does not match your registration. Please look straight, keep the same lighting, and try again.")

def _render_quiz_session_page(quiz_id, class_id):
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
                    _log_exam_entry_from_quiz_context(
                        {
                            "user_id": user_id,
                            "name": session.get("student_name") or "Unknown Student",
                            "email": session.get("email") or "",
                        },
                        row,
                        class_id,
                        _current_exam_entry_attempt_count(quiz_id),
                        "Locked",
                        "Max attempts reached before exam entry",
                    )
                    _reset_exam_entry_attempt_count(quiz_id)
                    return redirect_with_msg(
                        f"/stud-class-home/{class_id}",
                        f"❌ You have exhausted your {attempts_limit} attempt(s) for this quiz."
                    )
        except Exception as e:
            app.logger.warning(f"Error checking attempts limit: {type(e).__name__}: {str(e)}")

    _reset_exam_entry_attempt_count(quiz_id)

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


@app.route("/quiz_verified/<quiz_id>")
def quiz_verified_handoff(quiz_id):
    guard = student_required()
    if guard:
        return guard

    class_id = (session.get("active_class_id") or "").strip()
    if not class_id:
        return redirect_with_msg("/class-lists", "Please select your class first.")

    if _is_quiz_verified_for_session(quiz_id):
        session["pending_quiz_id"] = str(quiz_id)
        session.modified = True
    else:
        token_ok, token_reason = consume_quiz_verify_token(
            request.args.get("token") or request.args.get("verify_token") or "",
            str(quiz_id),
            class_id,
        )
        if not token_ok:
            return redirect_with_msg(
                url_for("quiz_verify"),
                f"Verification succeeded but quiz could not open ({token_reason}). Please verify again.",
            )

    # CHANGED: Do not render the quiz page under /quiz_verified.
    # Redirect to the real quiz-session URL so the browser leaves the camera
    # verification handoff route cleanly.
    print(
        f"[QUIZ-VERIFY-HANDOFF] redirecting_to_stud_quiz_session quiz_id={quiz_id}",
        flush=True,
    )
    return redirect(url_for("stud_quiz_session", quiz_id=str(quiz_id)))


@app.route("/stud-quiz-session/<quiz_id>")
def stud_quiz_session(quiz_id):
    guard = student_required()
    if guard:
        return guard

    class_id = (session.get("active_class_id") or "").strip()
    if not class_id:
        return redirect_with_msg("/class-lists", "Please select your class first.")

    if not _is_quiz_verified_for_session(quiz_id):
        token = request.args.get("verify_token") or request.args.get("token") or ""
        token_ok, token_reason = consume_quiz_verify_token(token, str(quiz_id), class_id)
        if not token_ok:
            return redirect_with_msg(
                url_for("quiz_verify"),
                f"Verification succeeded but quiz could not open ({token_reason}). Please verify again.",
            )

    return _render_quiz_session_page(str(quiz_id), class_id)


def _quiz_attempt_dt_iso(value):
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _quiz_attempt_answers(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _quiz_attempt_payload(row, submission_token, resumed=False):
    duration_seconds = int(row.get("duration_seconds") or 0)
    try:
        remaining_seconds = int(row.get("remaining_seconds") or 0)
    except Exception:
        remaining_seconds = 0

    return {
        "started": True,
        "resumed": bool(resumed),
        "attempt_id": str(row.get("attempt_id")),
        "submission_token": submission_token,
        "start_time": _quiz_attempt_dt_iso(row.get("started_at")),
        "expires_at": _quiz_attempt_dt_iso(row.get("expires_at")),
        "duration_seconds": max(0, duration_seconds),
        "remaining_seconds": max(0, remaining_seconds),
        "answers": _quiz_attempt_answers(row.get("answers_json")),
        "last_saved_at": _quiz_attempt_dt_iso(row.get("last_saved_at")),
    }


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
    time_limit_minutes = int(qrow.get("time_limit_minutes") or 60)
    duration_seconds = max(1, time_limit_minutes) * 60
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
                        duration_seconds = COALESCE(duration_seconds, %s),
                        expires_at = COALESCE(expires_at, started_at + (%s * INTERVAL '1 second')),
                        last_saved_at = COALESCE(last_saved_at, started_at)
                    WHERE attempt_id = %s
                    RETURNING
                        attempt_id,
                        started_at,
                        expires_at,
                        duration_seconds,
                        answers_json,
                        last_saved_at,
                        GREATEST(
                            0,
                            FLOOR(EXTRACT(EPOCH FROM (
                                COALESCE(expires_at, started_at + (%s * INTERVAL '1 second')) - NOW()
                            )))
                        )::int AS remaining_seconds;
                    """,
                    (
                        quiz_title,
                        total_points,
                        duration_seconds,
                        duration_seconds,
                        existing_attempt_id,
                        duration_seconds,
                    ),
                )
                attempt_row = cur.fetchone() or {}

                conn.commit()
                
                # SECURITY: Generate fresh submission token for reused attempt
                submission_token = pg_generate_submission_token(existing_attempt_id, user_id, quiz_id)
                
                print(
                    f"✅ Reused existing in-progress attempt: attempt_id={existing_attempt_id}, quiz_id={quiz_id}, user_id={user_id}",
                    flush=True
                )
                return ok(_quiz_attempt_payload(attempt_row, submission_token, resumed=True), "Attempt resumed")

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
                  (
                    attempt_id,
                    user_id,
                    quiz_id,
                    quiz_title,
                    score,
                    total_points,
                    started_at,
                    submitted_at,
                    answers_json,
                    attempt_number,
                    duration_seconds,
                    expires_at,
                    last_saved_at
                  )
                VALUES (%s, %s, %s, %s, 0, %s, NOW(), NULL, '{}'::jsonb, %s, %s, NOW() + (%s * INTERVAL '1 second'), NOW())
                RETURNING
                    attempt_id,
                    started_at,
                    expires_at,
                    duration_seconds,
                    answers_json,
                    last_saved_at,
                    GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (expires_at - NOW()))))::int AS remaining_seconds;
                """,
                (
                    str(attempt_id),
                    user_id,
                    quiz_id,
                    quiz_title,
                    total_points,
                    next_attempt_number,
                    duration_seconds,
                    duration_seconds,
                ),
            )
            attempt_row = cur.fetchone() or {}

            conn.commit()
            
            # SECURITY: Generate submission token for new attempt
            submission_token = pg_generate_submission_token(str(attempt_id), user_id, quiz_id)
            
            print(
                f"✅ New attempt created: attempt_id={attempt_id}, attempt_number={next_attempt_number}, quiz_id={quiz_id}, user_id={user_id}",
                flush=True
            )
            return ok(_quiz_attempt_payload(attempt_row, submission_token, resumed=False), "Attempt started")

    except Exception as e:
        app.logger.error(f"Quiz attempt start failed: {type(e).__name__}: {str(e)}")
        return fail("Operation failed", 500)


@app.route("/api/quiz-attempts/<attempt_id>/draft", methods=["POST"])
def api_quiz_attempt_draft(attempt_id):
    guard = student_required()
    if guard:
        return fail("Not logged in", 401)
    if not _require_csrf_json():
        return fail("CSRF failed", 400)

    data = request.get_json(silent=True) or {}
    quiz_id = (data.get("quizId") or "").strip()
    answers = data.get("answers") or {}
    if not quiz_id:
        return fail("Missing quizId", 400)
    if not isinstance(answers, dict):
        return fail("Invalid answers payload", 400)

    user_id = str(session.get("user_id"))
    class_id = (session.get("active_class_id") or "").strip()

    try:
        qrow = pg_get_quiz_by_id(quiz_id)
    except Exception:
        qrow = None

    if not qrow or str(qrow.get("class_id") or "") != str(class_id):
        return fail("Quiz not found for this class", 404)

    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE quiz_attempts
                SET answers_json = %s,
                    last_saved_at = NOW()
                WHERE attempt_id = %s
                  AND user_id = %s
                  AND quiz_id = %s
                  AND submitted_at IS NULL
                RETURNING
                    attempt_id,
                    started_at,
                    expires_at,
                    duration_seconds,
                    answers_json,
                    last_saved_at,
                    GREATEST(
                        0,
                        FLOOR(EXTRACT(EPOCH FROM (
                            COALESCE(expires_at, started_at + (COALESCE(duration_seconds, 3600) * INTERVAL '1 second')) - NOW()
                        )))
                    )::int AS remaining_seconds;
                """,
                (
                    psycopg2.extras.Json(answers),
                    str(attempt_id),
                    user_id,
                    quiz_id,
                ),
            )
            row = cur.fetchone()
            if not row:
                return fail("Open quiz attempt not found", 404)

            conn.commit()

        return ok(
            {
                "saved": True,
                "attempt_id": str(attempt_id),
                "last_saved_at": _quiz_attempt_dt_iso(row.get("last_saved_at")),
                "remaining_seconds": max(0, int(row.get("remaining_seconds") or 0)),
            },
            "Draft saved",
        )
    except Exception as e:
        app.logger.error(f"Quiz draft save failed: {type(e).__name__}: {str(e)}")
        return fail("Draft save failed", 500)


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

    # CHANGED:
    # REST-side attempt readiness guard.
    # Browser events such as tab switch / blur can arrive while the quiz page is
    # still loading. Do not insert a violation until the real attempt row exists.
    user_id = str(session.get("user_id") or "")
    attempt_key = _normalise_quiz_attempt_id(attempt_id)
    attempt_ready, attempt_reason = _quiz_attempt_is_ready(
        attempt_key,
        user_id=user_id,
        quiz_id=quiz_id or None,
        require_open=True,
    )
    if not attempt_ready:
        app.logger.warning(
            "Violation skipped because attempt is not ready: attempt_id=%s, user_id=%s, quiz_id=%s, reason=%s",
            attempt_key,
            user_id,
            quiz_id,
            attempt_reason,
        )
        return ok(
            _quiz_attempt_not_ready_payload(attempt_key, attempt_reason),
            "Violation skipped because quiz attempt is not ready",
        )

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
                    attempt_key,
                    vtype or "unknown",
                    ts,
                    time_remaining,
                ),
            )
            conn.commit()
    except Exception as e:
        app.logger.error(f"Violation insert failed: {type(e).__name__}: {str(e)}")
        return fail("Operation failed", 500)

    # CHANGED:
    # Window/tab activity is now emitted to the instructor monitor immediately
    # through Socket.IO from the browser. When ws_notified=True, keep the
    # database save but do not emit another delayed duplicate alert.
    if data.get("ws_notified") is True:
        return ok(
            {
                "saved": True,
                "ws_notified": True,
                "violation_type": vtype or "unknown",
            },
            "Violation recorded",
        )

    ws_payload = {  # CHANGED
        "attempt_id": attempt_key,  # CHANGED
        "class_id": class_id,  # CHANGED
        "quiz_id": quiz_id,  # CHANGED
        "event_type": "warning",  # CHANGED
        "violation_type": vtype or "unknown",  # CHANGED
        "timestamp": ts,  # CHANGED
        "time_remaining": time_remaining,  # CHANGED
    }  # CHANGED

    print(f"🚨 Emitting student warning: {ws_payload}", flush=True)
    _emit_student_warning(attempt_key, ws_payload)

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
                """
                SELECT 1
                FROM quiz_attempts
                WHERE attempt_id = %s
                  AND user_id = %s
                  AND quiz_id = %s
                LIMIT 1;
                """,
                (str(attempt_id), user_id, quiz_id),
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
                        last_saved_at = now(),
                        correct_answers_hash = %s
                    WHERE attempt_id = %s
                      AND user_id = %s
                      AND quiz_id = %s;
                    """,
                    (
                        quiz_title,
                        int(score),
                        int(total_points),
                        client_ip,
                        psycopg2.extras.Json(answers),
                        answers_hash,
                        str(attempt_id),
                        user_id,
                        quiz_id,
                    ),
                )
            else:
                # Insert new attempt
                cur.execute(
                    """
                    INSERT INTO quiz_attempts
                      (attempt_id, user_id, quiz_id, quiz_title, score, total_points, 
                       submitted_at, submitted_ip, submission_token_used, answers_json, 
                       last_saved_at, correct_answers_hash)
                    VALUES (%s, %s, %s, %s, %s, %s, now(), %s, TRUE, %s, now(), %s);
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

    _clear_quiz_verified_for_session(quiz_id)

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
    Continuous face monitoring endpoint called periodically during the quiz session.

    CHANGED:
    - Uses the same identity distance/threshold policy as quiz verification.
    - Uses monitoring-support embeddings when available, then falls back to front-only embeddings.
    - Uses simple best-match checking with the existing 85% confidence policy.
    - Uses MISMATCH_GRACE_COUNT before triggering face_mismatch blackout.
    - Resets mismatch counter when the face matches again.
    - Tolerates unstable motion/pose frames instead of counting them as mismatches.
    """
    guard = student_required()
    if guard:
        return fail("Not logged in", 401)

    if not _require_csrf_json():
        return fail("CSRF failed", 400)

    data = request.get_json(silent=True) or {}
    embedding = data.get("embedding")
    face_count = int(data.get("face_count") or 0)

    class_id = str(session.get("active_class_id") or "")
    quiz_id = str(data.get("quizId") or "")
    if not quiz_id:
        quiz_id = str(data.get("quiz_id") or "")

    # CHANGED:
    # REST face monitoring must not continue until the backend confirms that
    # the quiz attempt row exists for this student. This prevents REST-side
    # violation inserts from referencing an attempt_id that is not in quiz_attempts.
    user_id = str(session.get("user_id") or "")
    attempt_key = _normalise_quiz_attempt_id(attempt_id)
    attempt_ready, attempt_reason = _quiz_attempt_is_ready(
        attempt_key,
        user_id=user_id,
        quiz_id=quiz_id or None,
        require_open=True,
    )
    if not attempt_ready:
        app.logger.warning(
            "REST face-check skipped because attempt is not ready: attempt_id=%s, user_id=%s, quiz_id=%s, reason=%s",
            attempt_key,
            user_id,
            quiz_id,
            attempt_reason,
        )
        return ok(
            {
                **_quiz_attempt_not_ready_payload(attempt_key, attempt_reason),
                "confidence": None,
                "confidence_percent": None,
                "face_count": face_count,
            },
            "Monitoring skipped because quiz attempt is not ready",
        )

    def _log_violation(vtype: str):
        ready, reason = _quiz_attempt_is_ready(
            attempt_key,
            user_id=user_id,
            quiz_id=quiz_id or None,
            require_open=True,
        )
        if not ready:
            print(
                f"⏸️ REST violation skipped because attempt is not ready: "
                f"attempt_id={attempt_key}, type={vtype}, reason={reason}",
                flush=True,
            )
            return False

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
                        data.get("timeRemaining"),
                    ),
                )
                conn.commit()
                print(f"✅ Violation logged: {vtype} for attempt {attempt_key}", flush=True)
                return True
        except Exception as e:
            print(f"❌ Violation insert failed [{vtype}]: {str(e)}", flush=True)
            return False

    if face_count > 1:
        _log_violation("multiple_faces_detected")

        ws_payload = {
            "attempt_id": str(attempt_id),
            "class_id": class_id,
            "quiz_id": quiz_id,
            "event_type": "blackout_on",
            "violation_type": "multiple_faces_detected",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "face_count": face_count,
        }

        _emit_student_blackout_on(str(attempt_id), ws_payload)
        if class_id and quiz_id:
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

    if embedding is None or embedding == "no_face":
        ATTEMPT_NO_FACE_COUNT[attempt_key] = ATTEMPT_NO_FACE_COUNT.get(attempt_key, 0) + 1
        ATTEMPT_MISMATCH_COUNT[attempt_key] = 0
        ATTEMPT_MULTI_FACE_COUNT[attempt_key] = 0

        current_count = ATTEMPT_NO_FACE_COUNT[attempt_key]
        print(f"⚠️ REST no-face count {current_count}/{NO_FACE_PAUSE_COUNT} for attempt {attempt_key}", flush=True)

        if current_count < NO_FACE_WARNING_COUNT:
            return ok(
                {
                    "status": "monitoring_tolerated",
                    "reason": "temporary_no_face",
                    "confidence": 0.0,
                    "confidence_percent": 0.0,
                    "face_count": face_count,
                    "count": current_count,
                    "action": "tolerated",
                },
                "No face detected - within grace period"
            )

        if current_count == NO_FACE_WARNING_COUNT:
            return ok(
                {
                    "status": "monitoring_tolerated",
                    "reason": "temporary_no_face",
                    "confidence": 0.0,
                    "confidence_percent": 0.0,
                    "face_count": face_count,
                    "count": current_count,
                    "action": "tolerated",
                },
                "Temporary no-face tolerated"
            )

        if current_count >= NO_FACE_PAUSE_COUNT:
            _log_violation("no_face_pause")
            ATTEMPT_BLACKOUT_STATE[attempt_key] = True
            ATTEMPT_NO_FACE_COUNT[attempt_key] = 0

            ws_payload = {
                "attempt_id": attempt_key,
                "class_id": class_id,
                "quiz_id": quiz_id,
                "event_type": "blackout_on",
                "violation_type": "no_face_pause",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "face_count": face_count,
            }

            _emit_student_blackout_on(attempt_key, ws_payload)
            if class_id and quiz_id:
                _emit_instructor_violation_alert(class_id, quiz_id, ws_payload)

            return ok(
                {
                    "status": "no_face",
                    "confidence": 0.0,
                    "confidence_percent": 0.0,
                    "face_count": face_count,
                    "count": current_count,
                    "action": "blackout_on",
                },
                "No face detected - quiz paused"
            )

        return ok(
            {
                "status": "monitoring_tolerated",
                "reason": "temporary_no_face",
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
                "count": current_count,
                "action": "tolerated",
            },
            "No face detected - waiting before pause"
        )

    if not isinstance(embedding, list) or len(embedding) != 128:
        return fail("Invalid embedding format", 400)

    ATTEMPT_NO_FACE_COUNT[attempt_key] = 0
    ATTEMPT_MULTI_FACE_COUNT[attempt_key] = 0

    firebase_uid = str(session.get("firebase_uid") or "")
    if not firebase_uid:
        return fail("Missing Firebase UID in session", 401)

    # CHANGED: Continuous monitoring should use the monitoring-support profile.
    # This can include the normal front embeddings plus optional left/right
    # support embeddings collected silently during registration liveness.
    # Quiz-entry verification above still uses fb_get_embedding_enc(firebase_uid)
    # so it remains strict and front-facing.
    embedding_source = "monitoring_embeddings"
    try:
        enc_list = fb_get_monitor_embedding_enc(firebase_uid)
    except NameError:
        enc_list = []

    # CHANGED: Fallback to strict/front embeddings if monitoring-support
    # embeddings are unavailable or were skipped during registration.
    if not enc_list:
        embedding_source = "registered_embeddings"
        enc_list = fb_get_embedding_enc(firebase_uid)

    if not enc_list:
        return ok(
            {
                "status": "no_biometrics",
                "comparison": embedding_source,
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
            },
            "No biometrics registered"
        )

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
                "comparison": embedding_source,
                "confidence": 0.0,
                "confidence_percent": 0.0,
                "face_count": face_count,
            },
            "No valid biometrics"
        )

    # CHANGED:
    # REST monitoring should not count unstable movement/pose frames as
    # identity mismatches. This mirrors the Socket.IO monitoring behaviour.
    unstable_checker = globals().get("detect_identity_unstable_frame")
    if callable(unstable_checker):
        unstable_frame = unstable_checker(attempt_key, data)
        if unstable_frame:
            current_count = ATTEMPT_MISMATCH_COUNT.get(attempt_key, 0)
            return ok(
                {
                    "status": "monitoring_tolerated",
                    "reason": unstable_frame.get("reason") or "unstable_frame",
                    "comparison": embedding_source,
                    "confidence": None,
                    "confidence_percent": None,
                    "face_count": face_count,
                    "count": current_count,
                    "mismatch_count": current_count,
                    "mismatch_limit": MISMATCH_GRACE_COUNT,
                    "action": "tolerated",
                    "motion_details": unstable_frame.get("details", {}),
                },
                "Unstable movement frame tolerated"
            )

    # CHANGED:
    # Simple best-match policy for monitoring.
    # Compare the live embedding against the available monitoring/front
    # embeddings and accept based on the closest match using the existing
    # 85% confidence and hard distance boundary.
    stored_embedding_count = len(stored_embs or [])
    match_mode = "best_match"

    match_info = _quiz_best_match_summary(embedding, stored_embs)
    best_distance = match_info["best_distance"]
    matched = match_info["matched"]
    confidence = match_info["confidence"]
    matched_count = match_info["matched_count"]
    required_match_count = match_info["required_match_count"]
    distance_debug = match_info["distance_debug"]
    match_mode = match_info.get("match_policy_mode", "best_match")

    yaw_ratio = data.get("yaw_ratio")
    if yaw_ratio is None:
        yaw_ratio = data.get("yawRatio")

    # CHANGED:
    # REST continuous monitoring now uses simple best-match identity checking.
    # It still uses the same confidence and hard-distance boundaries.

    print(
        f"[MONITOR-EMBEDDING-DEBUG] attempt={attempt_id}, "
        f"yaw_ratio={yaw_ratio}, "
        f"all_distances={distance_debug}, "
        f"best_distance={best_distance:.4f}, "
        f"confidence={confidence:.2%}, "
        f"matched={matched}, policy=best_match, "
        f"source={embedding_source}, stored_count={stored_embedding_count}",
        flush=True,
    )

    # CHANGED:
    # If the user still matches while turning left/right, accept as same person.
    # Only tolerate the frame when matching fails AND yaw is too high.
    if not matched and should_skip_face_match_for_yaw(yaw_ratio):
        ATTEMPT_MISMATCH_COUNT[attempt_key] = 0

        print(
            f"↪️ Turned-face frame tolerated instead of mismatch: "
            f"attempt={attempt_key}, yaw_ratio={yaw_ratio}, "
            f"distance={best_distance:.4f}, confidence={confidence:.2%}, policy=best_match",
            flush=True,
        )

        return ok(
            {
                "status": "monitoring_tolerated",
                "reason": "turned_face_pose_unreliable",
                "confidence": round(float(confidence), 4),
                "confidence_percent": round(float(confidence) * 100, 2),
                "face_count": face_count,
                "yaw_ratio": yaw_ratio,
                "best_distance": round(float(best_distance), 4),
                "matched_count": matched_count,
                "required_match_count": required_match_count,
                "stored_embedding_count": stored_embedding_count,
                "match_policy_mode": match_mode,
                "comparison": embedding_source,
                "all_distances": distance_debug,
                "action": "tolerated",
            },
            "Face is turned; monitoring tolerated and will verify again on the next frame."
        )

    print(
        f"🔍 Face check: source={embedding_source}, distance={best_distance:.4f}, confidence={confidence:.2%}, "
        f"matched={matched}, policy=best_match, "
        f"stored_count={stored_embedding_count}, user={session.get('user_id')}",
        flush=True
    )

    if not matched:
        current_count = ATTEMPT_MISMATCH_COUNT.get(attempt_key, 0) + 1
        ATTEMPT_MISMATCH_COUNT[attempt_key] = current_count

        print(
            f"⚠️ REST face mismatch count {current_count}/{MISMATCH_GRACE_COUNT} "
            f"for attempt {attempt_key}: "
            f"distance={best_distance:.4f}, confidence={confidence:.2%}, policy=best_match",
            flush=True
        )

        if current_count < MISMATCH_GRACE_COUNT:
            return ok(
                {
                    "status": "monitoring_tolerated",
                    "reason": "temporary_face_mismatch",
                    "confidence": round(float(confidence), 4),
                    "confidence_percent": round(float(confidence) * 100, 2),
                    "face_count": face_count,
                    "count": current_count,
                    "required_count": MISMATCH_GRACE_COUNT,
                    "best_distance": round(float(best_distance), 4),
                    "matched_count": matched_count,
                    "required_match_count": required_match_count,
                    "stored_embedding_count": stored_embedding_count,
                    "match_policy_mode": match_mode,
                    "comparison": embedding_source,
                    "all_distances": distance_debug,
                    "action": "tolerated",
                },
                "Face mismatch tolerated temporarily"
            )

        ATTEMPT_MISMATCH_COUNT[attempt_key] = 0
        ATTEMPT_BLACKOUT_STATE[attempt_key] = True
        _log_violation("face_mismatch")

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
            "best_distance": round(float(best_distance), 4),
            "matched_count": matched_count,
            "required_match_count": required_match_count,
            "stored_embedding_count": stored_embedding_count,
            "match_policy_mode": match_mode,
            "comparison": embedding_source,
            "all_distances": distance_debug,
            "mismatch_count": MISMATCH_GRACE_COUNT,
        }

        print(f"🚨 Emitting student blackout_on: {ws_payload}", flush=True)
        _emit_student_blackout_on(attempt_key, ws_payload)

        if class_id and quiz_id:
            print(f"🚨 Emitting instructor violation_alert: room=class_{class_id}_quiz_{quiz_id}", flush=True)
            _emit_instructor_violation_alert(class_id, quiz_id, ws_payload)
        else:
            print(
                f"⚠️ Skipped instructor emit because class_id or quiz_id missing. "
                f"class_id={class_id}, quiz_id={quiz_id}",
                flush=True,
            )

    else:
        ATTEMPT_MISMATCH_COUNT[attempt_key] = 0

        motion_event = detect_tolerant_motion_event(attempt_key, data)
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

        print(f"✅ Emitting student blackout_off: {ws_payload}", flush=True)
        _emit_student_blackout_off(attempt_key, ws_payload)

    status = "match" if matched else "mismatch"

    return ok(
        {
            "status": status,
            "comparison": embedding_source,
            "confidence": round(float(confidence), 4),
            "confidence_percent": round(float(confidence) * 100, 2),
            "best_distance": round(float(best_distance), 4),
            "matched_count": matched_count,
            "required_match_count": required_match_count,
            "stored_embedding_count": stored_embedding_count,
            "match_policy_mode": match_mode,
            "all_distances": distance_debug,
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
