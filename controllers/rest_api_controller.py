"""Structured REST JSON API routes for users, classes, quizzes, sessions, attendance, and attempts."""

from ._shared import _set_liveness_running, load_app_context


load_app_context(globals())



def _api_count_quiz_questions(quiz):
    questions_data = (
        quiz.get("questions_json")
        or quiz.get("questionsJson")
        or quiz.get("questions")
        or quiz.get("questions_jsonb")
        or []
    )

    if isinstance(questions_data, str):
        try:
            questions_data = json.loads(questions_data)
        except Exception:
            questions_data = []

    if isinstance(questions_data, dict):
        inner_questions = questions_data.get("questions")
        if isinstance(inner_questions, list):
            return len(inner_questions)
        return 0

    if isinstance(questions_data, list):
        return len(questions_data)

    return 0


def _api_fix_quiz_question_counts(quizzes):
    for quiz in quizzes:
        quiz["question_count"] = _api_count_quiz_questions(quiz)
        quiz["questionCount"] = quiz["question_count"]
    return quizzes


@app.route("/api/classes/<class_id>/instructors", methods=["POST"])
def api_class_instructors_add(class_id):
    guard = admin_required()
    if guard:
        return jsonify({"success": False, "error": "Admin access only"}), 403

    data = request.get_json(silent=True) or {}
    instructor_ids = data.get("instructor_ids") or data.get("instructor_id")
    if isinstance(instructor_ids, str):
        instructor_ids = [instructor_ids]

    instructor_ids = [str(item).strip() for item in (instructor_ids or []) if str(item).strip()]
    if not instructor_ids:
        return jsonify({"success": False, "error": "Missing instructor_id"}), 400

    assigned = 0
    for instructor_id in instructor_ids:
        if pg_assign_instructor_to_class(instructor_id, class_id):
            assigned += 1

    return jsonify({
        "success": assigned > 0,
        "assigned": assigned,
        "message": f"{assigned} instructor(s) assigned successfully"
    })

@app.route("/api/classes/<class_id>/instructors/<instructor_id>", methods=["DELETE"])
def api_class_instructors_remove(class_id, instructor_id):
    guard = admin_required()
    if guard:
        return jsonify({"success": False, "error": "Admin access only"}), 403

    removed = pg_remove_instructor_from_class(instructor_id, class_id)

    if not removed:
        return jsonify({"success": False, "error": "Instructor was not assigned to this class"}), 404

    return jsonify({
        "success": True,
        "message": "Instructor removed successfully"
    })

@app.route("/api/classes/<class_id>/students", methods=["POST"])
def api_class_students_add(class_id):
    guard = admin_required()
    if guard:
        return jsonify({"success": False, "error": "Admin access only"}), 403

    data = request.get_json(silent=True) or {}
    student_ids = data.get("student_ids") or data.get("student_id")
    if isinstance(student_ids, str):
        student_ids = [student_ids]

    student_ids = [str(item).strip() for item in (student_ids or []) if str(item).strip()]
    if not student_ids:
        return jsonify({"success": False, "error": "Missing student_id"}), 400

    assigned = 0
    for student_id in student_ids:
        if pg_assign_student_to_class(student_id, class_id):
            assigned += 1

    return jsonify({
        "success": assigned > 0,
        "assigned": assigned,
        "message": f"{assigned} student(s) assigned successfully"
    })

@app.route("/api/classes/<class_id>/students/<student_id>", methods=["DELETE"])
def api_class_students_remove(class_id, student_id):
    guard = admin_required()
    if guard:
        return jsonify({"success": False, "error": "Admin access only"}), 403

    removed = pg_remove_student_from_class(student_id, class_id)

    if not removed:
        return jsonify({"success": False, "error": "Student was not assigned to this class"}), 404

    return jsonify({
        "success": True,
        "message": "Student removed successfully"
    })

@app.route("/api/users", methods=["GET"])
def api_users_get_all():
    guard = _api_require_admin()
    if guard:
        return guard

    role = (request.args.get("role") or "").strip().lower() or None
    users = pg_list_all_users(role=role, limit=2000)
    return _api_success(users, "Users loaded", 200)

@app.route("/api/users/<user_id>", methods=["GET"])
def api_users_get_one(user_id):
    guard = _api_require_admin()
    if guard:
        return guard

    user = pg_find_user_by_pg_id(str(user_id))
    if not user:
        return _api_error("User not found", 404)

    return _api_success(user, "User loaded", 200)

@app.route("/api/users", methods=["POST"])
def api_users_create():
    guard = _api_require_admin()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    first_name = (data.get("first_name") or data.get("firstName") or "").strip()
    last_name = (data.get("last_name") or data.get("lastName") or "").strip()
    email = (data.get("email") or "").strip()
    password = (data.get("password") or "").strip()
    role = (data.get("role") or "student").strip().lower()

    if role not in ("student", "instructor", "admin"):
        return _api_error("Role must be student, instructor, or admin", 400)

    if not first_name or not last_name or not email:
        return _api_error("first_name, last_name, and email are required", 400)

    try:
        firebase_uid = (data.get("firebase_uid") or "").strip()

        # Preferred: create the Firebase Auth user when password is provided.
        if password:
            fb_user = fb_auth.create_user(email=email, password=password)
            firebase_uid = fb_user.uid

        if not firebase_uid:
            return _api_error("password or firebase_uid is required to create a user", 400)

        full_name = f"{first_name} {last_name}".strip()
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (firebase_uid, first_name, last_name, full_name, email, role)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, firebase_uid, first_name, last_name, full_name, email, role;
                """,
                (firebase_uid, first_name, last_name, full_name, email, role),
            )
            created = cur.fetchone()
            conn.commit()

        return _api_success(created, "User created successfully", 201)

    except Exception as e:
        return _api_error(f"Failed to create user: {str(e)}", 500)

@app.route("/api/users/<user_id>", methods=["PATCH"])
def api_users_update(user_id):
    guard = _api_require_admin()
    if guard:
        return guard

    existing = pg_find_user_by_pg_id(str(user_id))
    if not existing:
        return _api_error("User not found", 404)

    data = request.get_json(silent=True) or {}
    first_name = (data.get("first_name") or data.get("firstName") or existing.get("first_name") or "").strip()
    last_name = (data.get("last_name") or data.get("lastName") or existing.get("last_name") or "").strip()
    email = (data.get("email") or existing.get("email") or "").strip()
    role = (data.get("role") or existing.get("role") or "student").strip().lower()

    if role not in ("student", "instructor", "admin"):
        return _api_error("Role must be student, instructor, or admin", 400)

    if not first_name or not last_name or not email:
        return _api_error("first_name, last_name, and email are required", 400)

    updated = pg_update_user(str(user_id), first_name, last_name, email, role)
    if not updated:
        return _api_error("User update failed", 404)

    return _api_success({"id": str(user_id)}, "User updated successfully", 200)

@app.route("/api/users/<user_id>", methods=["DELETE"])
def api_users_delete(user_id):
    guard = _api_require_admin()
    if guard:
        return guard

    deleted = pg_delete_user(str(user_id))
    if not deleted:
        return _api_error("User not found", 404)

    return _api_success({"id": str(user_id)}, "User deleted successfully", 200)

@app.route("/api/classes", methods=["GET"])
def api_classes_get_all():
    guard = _api_require_admin()
    if guard:
        return guard

    classes = pg_list_all_classes(limit=2000)
    return _api_success(classes, "Classes loaded", 200)

@app.route("/api/classes/<class_id>", methods=["GET"])
def api_classes_get_one(class_id):
    guard = _api_require_admin()
    if guard:
        return guard

    class_data = pg_get_class_by_id(str(class_id))
    if not class_data:
        return _api_error("Class not found", 404)

    return _api_success(class_data, "Class loaded", 200)

@app.route("/api/classes", methods=["POST"])
def api_classes_create():
    guard = _api_require_admin()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    class_code = (data.get("class_code") or data.get("classCode") or "").strip()
    section_name = (data.get("section_name") or data.get("sectionName") or "").strip()
    subject = (data.get("subject") or "").strip()
    description = (data.get("description") or "").strip()

    if not class_code or not section_name or not subject:
        return _api_error("class_code, section_name, and subject are required", 400)

    try:
        class_id = pg_create_class(class_code, section_name, subject, description)
        return _api_success({"id": class_id}, "Class created successfully", 201)
    except Exception as e:
        return _api_error(f"Failed to create class: {str(e)}", 500)

@app.route("/api/classes/<class_id>", methods=["PATCH"])
def api_classes_update(class_id):
    guard = _api_require_admin()
    if guard:
        return guard

    existing = pg_get_class_by_id(str(class_id))
    if not existing:
        return _api_error("Class not found", 404)

    data = request.get_json(silent=True) or {}
    class_code = (data.get("class_code") or data.get("classCode") or existing.get("class_code") or "").strip()
    section_name = (data.get("section_name") or data.get("sectionName") or existing.get("section_name") or "").strip()
    subject = (data.get("subject") or existing.get("subject") or "").strip()
    description = (data.get("description") if data.get("description") is not None else existing.get("description") or "").strip()

    if not class_code or not section_name or not subject:
        return _api_error("class_code, section_name, and subject are required", 400)

    updated = pg_update_class(str(class_id), class_code, section_name, subject, description)
    if not updated:
        return _api_error("Class update failed", 404)

    return _api_success({"id": str(class_id)}, "Class updated successfully", 200)

@app.route("/api/classes/<class_id>", methods=["DELETE"])
def api_classes_delete(class_id):
    guard = _api_require_admin()
    if guard:
        return guard

    deleted = pg_delete_class(str(class_id))
    if not deleted:
        return _api_error("Class not found", 404)

    return _api_success({"id": str(class_id)}, "Class deleted successfully", 200)

@app.route("/api/quizzes", methods=["GET"])
def api_quizzes_get_all():
    guard = _api_require_instructor_or_admin()
    if guard:
        return guard

    class_id = (request.args.get("class_id") or session.get("active_class_id") or "").strip()
    if not class_id:
        return _api_error("class_id is required", 400)

    if session.get("role") == "instructor":
        instructor_id = str(session.get("user_id") or "")
        if not pg_instructor_in_class(instructor_id, class_id):
            return _api_error("You are not assigned to this class", 403)

    quizzes = pg_list_quizzes_for_class(str(class_id), limit=500)
    quizzes = _api_fix_quiz_question_counts(quizzes)
    return _api_success(quizzes, "Quizzes loaded", 200)

@app.route("/api/quizzes/<quiz_id>", methods=["GET"])
def api_quizzes_get_one(quiz_id):
    guard = _api_require_instructor_or_admin()
    if guard:
        return guard

    quiz = pg_get_quiz_by_id(str(quiz_id))
    if not quiz:
        return _api_error("Quiz not found", 404)

    if session.get("role") == "instructor":
        instructor_id = str(session.get("user_id") or "")
        if not pg_instructor_in_class(instructor_id, str(quiz.get("class_id"))):
            return _api_error("You are not assigned to this class", 403)

    # Keep REST response compatible with the edit quiz frontend.
    # The database field is total_points, while the editor reads totalScore.
    total_points_value = quiz.get("total_points")
    if total_points_value is None:
        total_points_value = 0

    quiz["totalScore"] = total_points_value
    quiz["total_score"] = total_points_value
    quiz["totalPoints"] = total_points_value

    return _api_success(quiz, "Quiz loaded", 200)

@app.route("/api/quizzes", methods=["POST"])
def api_quizzes_create():
    guard = _api_require_instructor_or_admin()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    class_id = (data.get("class_id") or data.get("classId") or session.get("active_class_id") or "").strip()
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    total_points = _safe_int(data.get("total_points") or data.get("totalPoints"), 100)
    questions = data.get("questions_json") or data.get("questions") or []
    time_limit_minutes = _safe_int(data.get("time_limit_minutes") or data.get("timeLimitMinutes"), 60)
    attempts_type = (data.get("attempts_type") or data.get("attemptsType") or "unlimited").strip().lower()
    attempts_limit = data.get("attempts_limit") or data.get("attemptsLimit") or data.get("allowedAttempts")
    grade_method = (data.get("grade_method") or data.get("gradeMethod") or "highest").strip().lower()

    if grade_method not in ("highest", "latest"):
        grade_method = "highest"

    if not class_id or not title:
        return _api_error("class_id and title are required", 400)

    if session.get("role") == "instructor":
        instructor_id = str(session.get("user_id") or "")
        if not pg_instructor_in_class(instructor_id, class_id):
            return _api_error("You are not assigned to this class", 403)

    try:
        quiz_id = pg_create_quiz(
            class_id=str(class_id),
            created_by=str(session.get("user_id") or ""),
            title=title,
            description=description,
            total_points=total_points,
            questions=questions,
            time_limit_minutes=time_limit_minutes,
            attempts_type=attempts_type,
            attempts_limit=attempts_limit,
            grade_method=grade_method,
        )
        return _api_success({"id": quiz_id}, "Quiz created successfully", 201)
    except Exception as e:
        return _api_error(f"Failed to create quiz: {str(e)}", 500)

@app.route("/api/quizzes/<quiz_id>", methods=["PATCH"])
def api_quizzes_update(quiz_id):
    guard = _api_require_instructor_or_admin()
    if guard:
        return guard

    existing = pg_get_quiz_by_id(str(quiz_id))
    if not existing:
        return _api_error("Quiz not found", 404)

    if session.get("role") == "instructor":
        instructor_id = str(session.get("user_id") or "")
        if not pg_instructor_in_class(instructor_id, str(existing.get("class_id"))):
            return _api_error("You are not assigned to this class", 403)

    data = request.get_json(silent=True) or {}

    # RESTful activation update support:
    # PATCH /api/quizzes/<quiz_id> with {"is_active": true/false}
    # Handles JS booleans, strings, and numeric values safely.
    raw_active = data.get("is_active") if "is_active" in data else data.get("isActive")
    if raw_active is None:
        desired_active = None
    elif isinstance(raw_active, bool):
        desired_active = raw_active
    elif isinstance(raw_active, str):
        desired_active = raw_active.strip().lower() in ("true", "1", "yes", "on")
    else:
        desired_active = bool(raw_active)

    class_id = (data.get("class_id") or data.get("classId") or existing.get("class_id") or "").strip()
    title = (data.get("title") or existing.get("title") or "").strip()
    description = (data.get("description") if data.get("description") is not None else existing.get("description") or "").strip()
    # Quiz configuration should use total_points as the source of truth.
    # Do not fall back to total_score because that can be stale or unrelated
    # and may overwrite a 10-point quiz back to the default 100.
    total_points = _safe_int(
        data.get("total_points")
        or data.get("totalPoints")
        or data.get("totalScore")
        or existing.get("total_points"),
        0,
    )
    questions = data.get("questions_json") or data.get("questions") or existing.get("questions_json") or []
    time_limit_minutes = _safe_int(data.get("time_limit_minutes") or data.get("timeLimitMinutes") or existing.get("time_limit_minutes"), 60)
    attempts_type = (data.get("attempts_type") or data.get("attemptsType") or existing.get("attempts_type") or "unlimited").strip().lower()

    grade_method = (
        data.get("grade_method")
        or data.get("gradeMethod")
        or existing.get("grade_method")
        or "highest"
    ).strip().lower()

    if grade_method not in ("highest", "latest"):
        grade_method = "highest"

    attempts_limit = data.get("attempts_limit") if data.get("attempts_limit") is not None else data.get("attemptsLimit")
    if attempts_limit is None:
        attempts_limit = data.get("allowedAttempts")
    if attempts_limit is None:
        attempts_limit = existing.get("attempts_limit")

    updated = pg_update_quiz(
        quiz_id=str(quiz_id),
        class_id=str(class_id),
        title=title,
        description=description,
        total_points=total_points,
        questions=questions,
        updated_by=str(session.get("user_id") or ""),
        time_limit_minutes=time_limit_minutes,
        attempts_type=attempts_type,
        attempts_limit=attempts_limit,
        grade_method=grade_method,
        is_active=desired_active,
    )

    if not updated:
        return _api_error("Quiz update failed", 404)

    response_data = {"id": str(quiz_id)}
    if desired_active is not None:
        response_data["is_active"] = desired_active
        print(f"✅ REST PATCH quiz activation updated: quiz_id={quiz_id}, is_active={desired_active}", flush=True)

    return _api_success(response_data, "Quiz updated successfully", 200)

@app.route("/api/quizzes/<quiz_id>", methods=["DELETE"])
def api_quizzes_delete(quiz_id):
    guard = _api_require_instructor_or_admin()
    if guard:
        return guard

    existing = pg_get_quiz_by_id(str(quiz_id))
    if not existing:
        return _api_error("Quiz not found", 404)

    if session.get("role") == "instructor":
        instructor_id = str(session.get("user_id") or "")
        if not pg_instructor_in_class(instructor_id, str(existing.get("class_id"))):
            return _api_error("You are not assigned to this class", 403)

    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM quiz_attempt_violations
                WHERE attempt_id IN (
                    SELECT attempt_id FROM quiz_attempts WHERE quiz_id = %s
                );
                """,
                (str(quiz_id),),
            )
            cur.execute("DELETE FROM quiz_attempts WHERE quiz_id = %s;", (str(quiz_id),))
            cur.execute("DELETE FROM quizzes WHERE id = %s;", (str(quiz_id),))
            deleted_count = cur.rowcount
            conn.commit()

        if deleted_count <= 0:
            return _api_error("Quiz not found", 404)

        return _api_success({"id": str(quiz_id)}, "Quiz deleted successfully", 200)
    except Exception as e:
        return _api_error(f"Failed to delete quiz: {str(e)}", 500)

@app.route("/api/sessions", methods=["GET"])
def api_sessions_get_all():
    guard = _api_require_instructor_or_admin()
    if guard:
        return guard

    class_id = (request.args.get("class_id") or session.get("active_class_id") or "").strip()
    if not class_id:
        return _api_error("class_id is required", 400)

    if session.get("role") == "instructor":
        instructor_id = str(session.get("user_id") or "")
        if not pg_instructor_in_class(instructor_id, class_id):
            return _api_error("You are not assigned to this class", 403)

    sessions = pg_get_all_sessions_for_class(str(class_id))
    return _api_success(sessions, "Sessions loaded", 200)

@app.route("/api/sessions/<session_id>", methods=["GET"])
def api_sessions_get_one(session_id):
    guard = _api_require_instructor_or_admin()
    if guard:
        return guard

    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, class_id, session_date, present_start, present_until,
                   late_start, late_until, session_end, created_by, created_at,
                   start_date, end_date, COALESCE(is_all_day, FALSE) AS is_all_day
            FROM class_sessions
            WHERE id=%s
            LIMIT 1;
            """,
            (str(session_id),),
        )
        row = cur.fetchone()

    if not row:
        return _api_error("Session not found", 404)

    if session.get("role") == "instructor":
        instructor_id = str(session.get("user_id") or "")
        if not pg_instructor_in_class(instructor_id, str(row.get("class_id"))):
            return _api_error("You are not assigned to this class", 403)

    return _api_success(row, "Session loaded", 200)

@app.route("/api/sessions", methods=["POST"])
def api_sessions_create():
    guard = _api_require_instructor_or_admin()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    class_id = (data.get("class_id") or data.get("classId") or session.get("active_class_id") or "").strip()
    start_date_raw = (data.get("start_date") or data.get("startDate") or "").strip()
    end_date_raw = (data.get("end_date") or data.get("endDate") or start_date_raw).strip()
    is_all_day = bool(data.get("is_all_day") or data.get("isAllDay") or False)

    if not class_id or not start_date_raw:
        return _api_error("class_id and start_date are required", 400)

    if session.get("role") == "instructor":
        instructor_id = str(session.get("user_id") or "")
        if not pg_instructor_in_class(instructor_id, class_id):
            return _api_error("You are not assigned to this class", 403)

    try:
        start_date_value = datetime.strptime(start_date_raw, "%Y-%m-%d").date()
        end_date_value = datetime.strptime(end_date_raw, "%Y-%m-%d").date()

        present_start = _parse_time_hhmm(data.get("present_start") or data.get("presentStart") or "00:00")
        present_until = _parse_time_hhmm(data.get("present_until") or data.get("presentUntil") or "23:59")
        late_start = _parse_time_hhmm(data.get("late_start") or data.get("lateStart") or "00:00")
        late_until = _parse_time_hhmm(data.get("late_until") or data.get("lateUntil") or "23:59")
        session_end = _parse_time_hhmm(data.get("session_end") or data.get("sessionEnd") or "23:59")

        if not all([present_start, present_until, late_start, late_until, session_end]):
            return _api_error("Invalid time format. Use HH:MM", 400)

        pg_upsert_class_session(
            class_id=str(class_id),
            start_date=start_date_value,
            end_date=end_date_value,
            present_start=present_start,
            present_until=present_until,
            late_start=late_start,
            late_until=late_until,
            session_end=session_end,
            created_by=str(session.get("user_id") or ""),
            is_all_day=is_all_day,
        )

        return _api_success({"class_id": str(class_id)}, "Session saved successfully", 201)
    except Exception as e:
        return _api_error(f"Failed to save session: {str(e)}", 500)

@app.route("/api/sessions/<session_id>", methods=["PATCH"])
def api_sessions_update(session_id):
    guard = _api_require_instructor_or_admin()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}

    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM class_sessions WHERE id=%s LIMIT 1;", (str(session_id),))
        existing = cur.fetchone()

    if not existing:
        return _api_error("Session not found", 404)

    if session.get("role") == "instructor":
        instructor_id = str(session.get("user_id") or "")
        if not pg_instructor_in_class(instructor_id, str(existing.get("class_id"))):
            return _api_error("You are not assigned to this class", 403)

    try:
        start_date_raw = data.get("start_date") or data.get("startDate")
        end_date_raw = data.get("end_date") or data.get("endDate")

        start_date_value = datetime.strptime(start_date_raw, "%Y-%m-%d").date() if start_date_raw else existing.get("start_date")
        end_date_value = datetime.strptime(end_date_raw, "%Y-%m-%d").date() if end_date_raw else existing.get("end_date")

        present_start = _parse_time_hhmm(data.get("present_start") or data.get("presentStart")) or existing.get("present_start")
        present_until = _parse_time_hhmm(data.get("present_until") or data.get("presentUntil")) or existing.get("present_until")
        late_start = _parse_time_hhmm(data.get("late_start") or data.get("lateStart")) or existing.get("late_start")
        late_until = _parse_time_hhmm(data.get("late_until") or data.get("lateUntil")) or existing.get("late_until")
        session_end = _parse_time_hhmm(data.get("session_end") or data.get("sessionEnd")) or existing.get("session_end")
        is_all_day = data.get("is_all_day") if data.get("is_all_day") is not None else data.get("isAllDay")
        if is_all_day is None:
            is_all_day = existing.get("is_all_day") or False

        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE class_sessions
                   SET start_date=%s,
                       end_date=%s,
                       session_date=%s,
                       present_start=%s,
                       present_until=%s,
                       late_start=%s,
                       late_until=%s,
                       session_end=%s,
                       is_all_day=%s
                 WHERE id=%s;
                """,
                (
                    start_date_value,
                    end_date_value,
                    start_date_value,
                    present_start,
                    present_until,
                    late_start,
                    late_until,
                    session_end,
                    bool(is_all_day),
                    str(session_id),
                ),
            )
            conn.commit()

        return _api_success({"id": str(session_id)}, "Session updated successfully", 200)
    except Exception as e:
        return _api_error(f"Failed to update session: {str(e)}", 500)

@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def api_sessions_delete(session_id):
    guard = _api_require_instructor_or_admin()
    if guard:
        return guard

    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT class_id FROM class_sessions WHERE id=%s LIMIT 1;", (str(session_id),))
        existing = cur.fetchone()

    if not existing:
        return _api_error("Session not found", 404)

    if session.get("role") == "instructor":
        instructor_id = str(session.get("user_id") or "")
        if not pg_instructor_in_class(instructor_id, str(existing.get("class_id"))):
            return _api_error("You are not assigned to this class", 403)

    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM attendance_records WHERE session_id=%s;", (str(session_id),))
        cur.execute("DELETE FROM class_sessions WHERE id=%s;", (str(session_id),))
        deleted_count = cur.rowcount
        conn.commit()

    if deleted_count <= 0:
        return _api_error("Session not found", 404)

    return _api_success({"id": str(session_id)}, "Session deleted successfully", 200)

@app.route("/api/attendance", methods=["GET"])
def api_attendance_get():
    guard = _api_require_instructor_or_admin()
    if guard:
        return guard

    class_id = (request.args.get("class_id") or session.get("active_class_id") or "").strip()
    session_id = (request.args.get("session_id") or "").strip()
    selected_date_raw = (request.args.get("date") or "").strip()
    selected_date = None

    if selected_date_raw:
        try:
            selected_date = datetime.strptime(selected_date_raw, "%Y-%m-%d").date()
        except Exception:
            return _api_error("Invalid date format. Use YYYY-MM-DD", 400)

    if not class_id:
        return _api_error("class_id is required", 400)

    if session.get("role") == "instructor":
        instructor_id = str(session.get("user_id") or "")
        if not pg_instructor_in_class(instructor_id, class_id):
            return _api_error("You are not assigned to this class", 403)

    active_session = None

    if selected_date:
        active_session = pg_get_active_session_for_date(str(class_id), selected_date)

    if session_id:
        roster = pg_list_attendance_roster_for_session(
            str(class_id),
            str(session_id),
            limit=5000,
        )
    else:
        roster = pg_list_attendance_records_for_class(
            str(class_id),
            selected_date,
            limit=5000,
        )

        if selected_date:
            roster = [
                r for r in roster
                if r.get("attendance_date") == selected_date
                or str(r.get("attendance_date") or "") == selected_date_raw
            ]

    # Real attendance activity should only count actual marked/verified records.
    # Default roster rows shown as Absent must not be treated as historical records.
    activity_records = [
        r for r in (roster or [])
        if (
            str(r.get("status") or "").lower() in ["present", "late"]
            or bool(r.get("verified"))
            or bool(r.get("time_in"))
            or bool(r.get("marked_at"))
            or bool(r.get("verified_at"))
        )
    ]

    session_exists = bool(session_id or active_session)
    attendance_activity_exists = bool(activity_records)

    return _api_success(
        {
            "class_id": str(class_id),
            "session_id": str(session_id) if session_id else None,
            "date": selected_date_raw or None,
            "session_exists": session_exists,
            "attendance_activity_exists": attendance_activity_exists,
            "no_session_found": not session_exists and not attendance_activity_exists,
            "records": roster,
        },
        "Attendance loaded",
        200,
    )

@app.route("/api/attendance", methods=["POST"])
def api_attendance_create():
    guard = _api_require_instructor_or_admin()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or data.get("student_id") or data.get("studentId") or "").strip()
    class_id = (data.get("class_id") or data.get("classId") or session.get("active_class_id") or "").strip()
    session_id = (data.get("session_id") or data.get("sessionId") or "").strip()
    status = (data.get("status") or "Present").strip().capitalize()

    if status not in ("Present", "Late", "Absent"):
        return _api_error("status must be Present, Late, or Absent", 400)

    if not user_id or not class_id or not session_id:
        return _api_error("user_id, class_id, and session_id are required", 400)

    if session.get("role") == "instructor":
        instructor_id = str(session.get("user_id") or "")
        if not pg_instructor_in_class(instructor_id, class_id):
            return _api_error("You are not assigned to this class", 403)

    ok_saved, message = pg_mark_attendance_for_session(str(user_id), str(class_id), str(session_id), status)
    if not ok_saved:
        return _api_error(message or "Failed to save attendance", 500)

    return _api_success({"user_id": user_id, "class_id": class_id, "session_id": session_id, "status": status}, message, 201)

@app.route("/api/attendance/<student_id>", methods=["PATCH"])
def api_attendance_update(student_id):
    # Attendance uses UPSERT, so PATCH performs the same save operation as POST.
    data = request.get_json(silent=True) or {}
    data["student_id"] = str(student_id)
    request._cached_json = (data, data)
    return api_attendance_create()

@app.route("/api/attendance/<student_id>", methods=["DELETE"])
def api_attendance_delete(student_id):
    guard = _api_require_instructor_or_admin()
    if guard:
        return guard

    class_id = (request.args.get("class_id") or session.get("active_class_id") or "").strip()
    session_id = (request.args.get("session_id") or "").strip()

    if not class_id or not session_id:
        return _api_error("class_id and session_id are required", 400)

    if session.get("role") == "instructor":
        instructor_id = str(session.get("user_id") or "")
        if not pg_instructor_in_class(instructor_id, class_id):
            return _api_error("You are not assigned to this class", 403)

    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM attendance_records
            WHERE student_id=%s AND class_id=%s AND session_id=%s;
            """,
            (str(student_id), str(class_id), str(session_id)),
        )
        deleted_count = cur.rowcount
        conn.commit()

    if deleted_count <= 0:
        return _api_error("Attendance record not found", 404)

    return _api_success({"student_id": str(student_id)}, "Attendance deleted successfully", 200)

@app.route("/api/students/<student_id>", methods=["PATCH"])
def api_students_update(student_id):
    guard = admin_required()
    if guard:
        return jsonify({"success": False, "error": "Admin access only"}), 403

    data = request.get_json(silent=True) or {}

    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    email = (data.get("email") or "").strip()

    if not first_name or not last_name or not email:
        return jsonify({
            "success": False,
            "error": "First name, last name, and email are required"
        }), 400

    # Supports both users.id (PostgreSQL UUID) and users.firebase_uid.
    row = pg_find_user_by_pg_id(str(student_id)) or pg_find_user_by_firebase_uid(str(student_id))
    if not row:
        return jsonify({
            "success": False,
            "error": "Student not found"
        }), 404

    role = row.get("role", "") if isinstance(row, dict) else getattr(row, "role", "")
    if role != "student":
        return jsonify({
            "success": False,
            "error": "Selected user is not a student"
        }), 400

    real_student_id = str(row["id"] if isinstance(row, dict) else getattr(row, "id"))

    try:
        updated = pg_update_user(
            real_student_id,
            first_name,
            last_name,
            email,
            "student",
        )

        if not updated:
            return jsonify({
                "success": False,
                "error": "Student not found or update failed"
            }), 404

        return jsonify({
            "success": True,
            "message": "Student updated successfully"
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Failed to update student: {str(e)}"
        }), 500

@app.route("/api/students/<student_id>", methods=["DELETE"])
def api_students_delete(student_id):
    guard = admin_required()
    if guard:
        return jsonify({"success": False, "error": "Admin access only"}), 403

    current_user_id = str(session.get("user_id") or "")

    # Supports both users.id (PostgreSQL UUID) and users.firebase_uid.
    row = pg_find_user_by_pg_id(str(student_id)) or pg_find_user_by_firebase_uid(str(student_id))
    if not row:
        return jsonify({
            "success": False,
            "error": "Student not found"
        }), 404

    real_student_id = str(row["id"] if isinstance(row, dict) else getattr(row, "id"))

    if real_student_id == current_user_id or str(student_id) == current_user_id:
        return jsonify({
            "success": False,
            "error": "You cannot delete your own account"
        }), 400

    role = row.get("role", "") if isinstance(row, dict) else getattr(row, "role", "")
    if role != "student":
        return jsonify({
            "success": False,
            "error": "Selected user is not a student"
        }), 400

    try:
        deleted = pg_delete_user(real_student_id)

        if not deleted:
            return jsonify({
                "success": False,
                "error": "Student delete failed"
            }), 404

        return jsonify({
            "success": True,
            "message": "Student deleted successfully"
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Failed to delete student: {str(e)}"
        }), 500

@app.route("/api/attempts/<attempt_id>", methods=["GET"])
def api_attempt_get(attempt_id):
    if not session.get("logged_in"):
        return fail("Authentication required", 401)

    def _norm(value):
        return str(value).strip() if value is not None else ""

    def _get_answer(answers_dict, qid):
        if qid is None:
            return None

        str_key = str(qid)

        if str_key in answers_dict:
            return answers_dict[str_key]

        try:
            int_key = int(str_key)
            if int_key in answers_dict:
                return answers_dict[int_key]
        except Exception:
            pass

        return None

    def _choice_text(choice):
        if isinstance(choice, dict):
            return choice.get("text") or choice.get("label") or choice.get("value") or ""
        return str(choice) if choice is not None else ""

    def _student_answer_display(question, student_answer):
        qtype = question.get("type") or "text"

        if student_answer is None or student_answer == "":
            return ""

        if qtype == "multiple-choice":
            choices = question.get("choices") or []

            if isinstance(student_answer, list):
                labels = []
                for item in student_answer:
                    try:
                        idx = int(item)
                        if 0 <= idx < len(choices):
                            labels.append(_choice_text(choices[idx]))
                        else:
                            labels.append(str(item))
                    except Exception:
                        labels.append(str(item))
                return ", ".join(labels)

            try:
                idx = int(student_answer)
                if 0 <= idx < len(choices):
                    return _choice_text(choices[idx])
            except Exception:
                pass

            return str(student_answer)

        if qtype == "matching" and isinstance(student_answer, dict):
            left_items = question.get("leftItems") or []
            right_items = question.get("rightItems") or []
            pairs = []

            for left_idx, selected_right_idx in student_answer.items():
                try:
                    left_text = left_items[int(left_idx)]
                    right_text = right_items[int(selected_right_idx)]
                    pairs.append(f"{left_text} -> {right_text}")
                except Exception:
                    pairs.append(f"{left_idx} -> {selected_right_idx}")

            return "; ".join(pairs)

        if isinstance(student_answer, (dict, list)):
            return json.dumps(student_answer)

        return str(student_answer)

    def _correct_answer_display(question):
        qtype = question.get("type") or "text"

        if qtype == "multiple-choice":
            choices = question.get("choices") or []
            correct_indexes = question.get("correctAnswers") or []

            if not isinstance(correct_indexes, list):
                correct_indexes = []

            labels = []
            for idx in correct_indexes:
                try:
                    idx = int(idx)
                    if 0 <= idx < len(choices):
                        labels.append(_choice_text(choices[idx]))
                except Exception:
                    pass

            return ", ".join(labels)

        if qtype == "true-false":
            return question.get("correctAnswer") or ""

        if qtype in ("identification", "numerical"):
            return question.get("correctAnswer") or ""

        if qtype == "matching":
            left_items = question.get("leftItems") or []
            right_items = question.get("rightItems") or []
            correct = question.get("correctAnswers") or []

            pairs = []
            for left_idx, right_idx in enumerate(correct):
                try:
                    left_text = left_items[left_idx]
                    right_text = right_items[int(right_idx)]
                    pairs.append(f"{left_text} -> {right_text}")
                except Exception:
                    pass

            return "; ".join(pairs)

        return question.get("correct_answer") or question.get("answer") or ""

    def _grade_one(question, student_answer):
        qtype = _norm(question.get("type"))
        try:
            points = float(question.get("points") or 0)
        except Exception:
            points = 0.0

        if points < 0:
            points = 0.0

        if student_answer is None or student_answer == "":
            return 0.0

        if qtype == "multiple-choice":
            correct = question.get("correctAnswers") or []
            if not isinstance(correct, list):
                correct = []

            try:
                correct_indexes = sorted([int(x) for x in correct])
            except Exception:
                correct_indexes = []

            if isinstance(student_answer, list):
                try:
                    selected_indexes = sorted([int(x) for x in student_answer])
                    return points if selected_indexes == correct_indexes else 0.0
                except Exception:
                    return 0.0

            try:
                selected_idx = int(student_answer)
                return points if [selected_idx] == correct_indexes else 0.0
            except Exception:
                return 0.0

        if qtype == "true-false":
            correct_answer = _norm(question.get("correctAnswer")).lower()
            student_value = _norm(student_answer).lower()
            return points if correct_answer and student_value == correct_answer else 0.0

        if qtype == "identification":
            correct_answer = _norm(question.get("correctAnswer"))
            student_value = _norm(student_answer)
            case_insensitive = question.get("caseInsensitive")
            if case_insensitive is None:
                case_insensitive = True

            if bool(case_insensitive):
                return points if correct_answer and student_value.lower() == correct_answer.lower() else 0.0

            return points if correct_answer and student_value == correct_answer else 0.0

        if qtype == "numerical":
            try:
                correct_answer = float(question.get("correctAnswer"))
                student_value = float(student_answer)
                tolerance = float(question.get("tolerance") or 0)
                return points if abs(student_value - correct_answer) <= tolerance else 0.0
            except Exception:
                return 0.0

        if qtype == "matching":
            correct = question.get("correctAnswers") or []
            if not isinstance(correct, list) or not isinstance(student_answer, dict):
                return 0.0

            for idx, expected in enumerate(correct):
                actual = student_answer.get(str(idx), student_answer.get(idx))
                try:
                    if int(actual) != int(expected):
                        return 0.0
                except Exception:
                    return 0.0

            return points

        if qtype == "image-answer":
            if isinstance(student_answer, dict):
                return float(student_answer.get("graded_score") or 0)
            return 0.0

        return 0.0

    try:
        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    qa.attempt_id,
                    qa.answers_json,
                    qa.user_id,
                    qa.score,
                    qa.total_points,
                    q.questions_json,
                    u.full_name
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

            current_user_id = str(session.get("user_id") or "")
            current_role = str(session.get("role") or "")
            attempt_user_id = str(result.get("user_id") or "")

            if current_role != "instructor" and current_user_id != attempt_user_id:
                return fail("You don't have permission to view this attempt", 403)

            answers_json = result.get("answers_json") or {}
            questions_json = result.get("questions_json") or {}
            student_name = result.get("full_name") or "Unknown"
            attempt_score = float(result.get("score") or 0)
            attempt_total = float(result.get("total_points") or 0)

            if isinstance(answers_json, str):
                answers_json = json.loads(answers_json)

            if isinstance(questions_json, str):
                questions_json = json.loads(questions_json)

            answers_array = []
            questions_list = questions_json.get("questions", []) if isinstance(questions_json, dict) else questions_json

            if not isinstance(questions_list, list):
                questions_list = []

            for i, question in enumerate(questions_list):
                q_id = question.get("id") or str(i)
                student_answer_raw = _get_answer(answers_json, q_id)

                saved_score = (
                    answers_json.get(f"{q_id}_score")
                    or answers_json.get(f"{str(q_id)}_score")
                    or answers_json.get(f"{q_id}_student_score")
                )

                if saved_score is not None:
                    try:
                        student_score = float(saved_score)
                    except Exception:
                        student_score = 0.0
                else:
                    if len(questions_list) == 1 and attempt_total > 0:
                        student_score = attempt_score
                    else:
                        student_score = _grade_one(question, student_answer_raw)

                try:
                    question_points = float(question.get("points") or 0)
                except Exception:
                    question_points = 0.0

                answers_array.append({
                    "question_id": str(q_id),
                    "question": question.get("question_text") or question.get("text") or f"Question {i + 1}",
                    "question_type": question.get("type") or "text",
                    "student_answer": _student_answer_display(question, student_answer_raw),
                    "raw_student_answer": student_answer_raw,
                    "correct_answer": _correct_answer_display(question),
                    "points": question.get("points") or 0,
                    "choices": question.get("choices") or [],
                    "student_score": student_score,
                    "is_correct": student_score >= question_points if question_points > 0 else False
                })

            return ok({
                "student_name": student_name,
                "attempt_score": attempt_score,
                "total_points": attempt_total,
                "override_reason": answers_json.get("override_reason", ""),
                "overridden_at": answers_json.get("overridden_at", ""),
                "answers": answers_array
            }, "Student answers retrieved", 200)

    except Exception as e:
        print(f"Error in api_attempt_get: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        return fail(f"Error retrieving student answer: {str(e)}", 500)

@app.route("/api/attempts/<attempt_id>", methods=["PATCH"])
def api_attempt_update(attempt_id):
    if not session.get("logged_in") or session.get("role") != "instructor":
        return fail("Instructor access required", 401)

    if not _require_csrf_json():
        return fail("CSRF validation failed", 400)

    try:
        data = request.get_json(silent=True) or {}
        new_score = data.get("new_score")
        reason = data.get("reason", "")
        question_scores = data.get("question_scores", {})

        if new_score is None:
            return fail("New score is required", 400)

        new_score = float(new_score)

        if new_score < 0:
            return fail("Score cannot be negative", 400)

        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
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

            if isinstance(answers_json, str):
                answers_json = json.loads(answers_json)

            if question_scores:
                for q_id, score in question_scores.items():
                    answers_json[f"{q_id}_score"] = float(score)

            # Save instructor override reason inside answers_json so the student
            # can see the feedback when reviewing the quiz attempt.
            reason = str(reason or "").strip()
            if reason:
                answers_json["override_reason"] = reason
                answers_json["overridden_at"] = datetime.now().isoformat(timespec="seconds")
            else:
                answers_json.pop("override_reason", None)
                answers_json.pop("overridden_at", None)

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
        print(f"Error in api_attempt_update: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        return fail(f"Error updating grade: {str(e)}", 500)
