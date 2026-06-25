"""Instructor class, session, quiz, grade, and monitor routes."""

from ._shared import _set_liveness_running, load_app_context


load_app_context(globals())


def _instructor_count_quiz_questions_from_json(questions_json):
    """
    Counts quiz questions from both supported saved formats:
    1. questions_json = [{"id": ...}, ...]
    2. questions_json = {"questions": [{"id": ...}, ...]}
    """
    if isinstance(questions_json, str):
        try:
            questions_json = json.loads(questions_json)
        except Exception:
            questions_json = []

    if isinstance(questions_json, dict):
        questions = questions_json.get("questions", [])
    elif isinstance(questions_json, list):
        questions = questions_json
    else:
        questions = []

    return len(questions) if isinstance(questions, list) else 0


def _instructor_fix_quiz_card_question_counts(quizzes):
    """
    Re-checks each quiz card against the saved quiz row so Class Home displays
    the correct Questions count after editing.
    """
    fixed_quizzes = []

    for quiz in quizzes or []:
        quiz_dict = dict(quiz)

        quiz_id = str(
            quiz_dict.get("id")
            or quiz_dict.get("quiz_id")
            or quiz_dict.get("quizId")
            or ""
        ).strip()

        question_count = None

        # First try any questions_json already included in the quiz card.
        if (
            "questions_json" in quiz_dict
            or "questionsJson" in quiz_dict
            or "questions" in quiz_dict
            or "questions_jsonb" in quiz_dict
        ):
            question_count = _instructor_count_quiz_questions_from_json(
                quiz_dict.get("questions_json")
                or quiz_dict.get("questionsJson")
                or quiz_dict.get("questions")
                or quiz_dict.get("questions_jsonb")
            )

        # If the quiz card does not include questions_json, fetch the full quiz row.
        if question_count is None and quiz_id:
            try:
                full_quiz = pg_get_quiz_by_id(quiz_id)
                if full_quiz:
                    question_count = _instructor_count_quiz_questions_from_json(
                        full_quiz.get("questions_json")
                        or full_quiz.get("questionsJson")
                        or full_quiz.get("questions")
                        or full_quiz.get("questions_jsonb")
                    )
            except Exception:
                question_count = quiz_dict.get("question_count") or quiz_dict.get("questionCount") or 0

        if question_count is None:
            question_count = quiz_dict.get("question_count") or quiz_dict.get("questionCount") or 0

        quiz_dict["question_count"] = int(question_count or 0)
        quiz_dict["questionCount"] = quiz_dict["question_count"]

        fixed_quizzes.append(quiz_dict)

    return fixed_quizzes

def _validate_instructor_quiz_payload(questions):
    """
    Backend validation for quiz create/update.
    This protects data integrity even if frontend validation is bypassed.
    """
    if not isinstance(questions, list) or not questions:
        return "At least one question is required"

    for idx, q in enumerate(questions, start=1):
        if not isinstance(q, dict):
            return f"Question {idx}: invalid question format"

        q_type = str(q.get("type") or "").strip().lower()
        q_text = str(q.get("text") or q.get("question") or q.get("question_text") or "").strip()

        try:
            q_points = int(q.get("points") or 0)
        except Exception:
            q_points = 0

        if not q_type:
            return f"Question {idx}: question type is required"

        if not q_text:
            return f"Question {idx}: question text is required"

        if q_points <= 0:
            return f"Question {idx}: points must be greater than 0"

        if q_type in ("multiple-choice", "multiple_choice"):
            choices = q.get("choices") or []
            correct_answers = q.get("correctAnswers") or q.get("correct_answers") or []

            if not isinstance(choices, list) or len(choices) < 2:
                return f"Question {idx}: at least 2 choices are required"

            for cidx, choice in enumerate(choices, start=1):
                if isinstance(choice, dict):
                    choice_text = str(choice.get("text") or choice.get("label") or choice.get("value") or "").strip()
                else:
                    choice_text = str(choice or "").strip()

                if not choice_text:
                    return f"Question {idx}: choice {cidx} cannot be blank"

            if not isinstance(correct_answers, list) or not correct_answers:
                return f"Question {idx}: mark at least one correct answer"

        elif q_type == "true-false":
            answer = str(q.get("correctAnswer") or q.get("correct_answer") or "").strip().lower()
            if answer not in ("true", "false"):
                return f"Question {idx}: select true or false as the correct answer"

        elif q_type == "identification":
            answer = str(q.get("correctAnswer") or q.get("correct_answer") or "").strip()
            if not answer:
                return f"Question {idx}: correct answer is required"

        elif q_type == "numerical":
            answer = q.get("correctAnswer", q.get("correct_answer"))
            try:
                float(answer)
            except Exception:
                return f"Question {idx}: valid numerical answer required"

            tolerance = q.get("tolerance")
            try:
                tolerance_value = float(tolerance)
                if tolerance_value < 0:
                    raise ValueError()
            except Exception:
                return f"Question {idx}: valid tolerance required"

        elif q_type == "matching":
            left_items = q.get("leftItems") or q.get("left_items") or []
            right_items = q.get("rightItems") or q.get("right_items") or []

            if not isinstance(left_items, list) or not isinstance(right_items, list):
                return f"Question {idx}: matching pairs are invalid"

            if len(left_items) < 2 or len(right_items) < 2:
                return f"Question {idx}: matching requires at least 2 pairs"

            if len(left_items) != len(right_items):
                return f"Question {idx}: matching pairs mismatch"

            for pair_idx, (left, right) in enumerate(zip(left_items, right_items), start=1):
                if not str(left or "").strip() or not str(right or "").strip():
                    return f"Question {idx}: matching pair {pair_idx} cannot be blank"

        elif q_type == "image-answer":
            # Image answer questions only need question text and points.
            pass

        else:
            return f"Question {idx}: unsupported question type"

    return None




@app.route("/instructor/attendance/<class_id>")
def instructor_attendance(class_id=None):
    guard = instructor_required()
    if guard:
        return guard

    if not class_id:
        class_id = session.get("active_class_id") or pg_first_instructor_class_id(str(session.get("user_id") or ""))
        if not class_id:
            return redirect_with_msg("/class-lists", "Please select a class first.")

    cmeta, resp = _require_instructor_class_or_redirect(str(class_id))
    if resp:
        return resp

    # Allow the instructor to filter attendance records by date.
    # Example: /instructor/attendance/<class_id>?date=2026-05-15
    selected_date_str = (request.args.get("date") or str(app_today())).strip()
    try:
        selected_date = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
    except Exception:
        selected_date = app_today()
        selected_date_str = str(selected_date)

    # Show the session window that is active for the selected date.
    sess = pg_get_active_session_for_date(str(class_id), selected_date)

    # Only load attendance rows when:
    # 1. a real session exists for the selected date, or
    # 2. actual historical attendance activity exists.
    #
    # This prevents the page from showing the full class roster as "Absent"
    # when no attendance session exists for that date.
    roster = []

    if sess:
        roster = pg_list_attendance_records_for_class(
            str(class_id),
            selected_date,
            session_id=str(sess.get("id") or "")
        )
    else:
        historical_roster = pg_list_attendance_records_for_class(
            str(class_id),
            selected_date
        )

        marked_records = [
            r for r in historical_roster
            if (
                str(r.get("status") or "").lower() in ["present", "late"]
                or bool(r.get("verified"))
                or bool(r.get("time_in"))
                or bool(r.get("attendance_time"))
                or bool(r.get("marked_at"))
                or bool(r.get("verified_at"))
            )
        ]

        if marked_records:
            sess = {
                "historical": True,
                "session_date": selected_date,
            }
            # Historical view has no active session selected, so keep the
            # existing date-based records for audit/history visibility.
            roster = historical_roster

    return render_template(
        "instructor_attendance.html",
        class_meta=cmeta,
        today_session=sess,
        roster=roster,
        selected_date=selected_date_str,
        active_class_id=str(cmeta["id"]),
        active_page="attendance",
    )

@app.route("/instructor/class-home")
@app.route("/instructor/class-home/<class_id>")
def instructor_class_home(class_id=None):
    guard = instructor_required()
    if guard:
        return guard

    instructor_id = str(session.get("user_id") or "")

    if not class_id:
        class_id = session.get("active_class_id") or pg_first_instructor_class_id(instructor_id)
        if not class_id:
            return redirect_with_msg("/class-lists", "No classes assigned to you yet.")

    if not pg_instructor_in_class(instructor_id, class_id):
        return redirect_with_msg("/class-lists", "You are not assigned to this class.")

    cmeta = pg_get_class_by_id(class_id)
    if not cmeta:
        return redirect_with_msg("/class-lists", "Class not found.")

    session["active_class_id"] = str(cmeta["id"])
    session["active_class_name"] = f"{cmeta['section_name']} ({cmeta['class_code']})"

    sess = pg_get_active_session_for_date(str(cmeta["id"]), app_today())

    try:
        quizzes = _build_quiz_cards_for_class(str(cmeta["id"]), is_instructor=True)
        quizzes = _instructor_fix_quiz_card_question_counts(quizzes)
    except Exception as e:
        print(f"ERROR loading instructor quiz cards: {str(e)}", flush=True)
        quizzes = []

    return render_template(
        "instructor_class_home.html",
        section_name=session.get("active_class_name") or cmeta["section_name"],
        quizzes=quizzes,
        today_session=sess,
        active_class_id=str(cmeta["id"]),
        active_class_name=f"{cmeta['section_name']} ({cmeta['class_code']})",
        active_page="class_home",
    )

@app.route("/instructor/sessions", methods=["GET"])
def instructor_sessions():
    guard = instructor_required()
    if guard:
        return guard

    instructor_id = str(session.get("user_id") or "")
    classes = pg_list_instructor_classes(instructor_id)
    if not classes:
        return redirect_with_msg("/class-lists", "No classes assigned to you yet.")

    selected_class_id = (request.args.get("class_id") or session.get("active_class_id") or str(classes[0]["id"])).strip()
    if not pg_instructor_in_class(instructor_id, selected_class_id):
        return redirect_with_msg("/class-lists", "You are not assigned to this class.")

    # Auto-cleanup expired sessions
    pg_cleanup_expired_sessions(selected_class_id)

    cmeta = pg_get_class_by_id(selected_class_id)
    today_value = app_today()
    sess = pg_get_active_session_for_date(selected_class_id, today_value)
    all_sessions = pg_get_all_sessions_for_class(selected_class_id)

    classes_dropdown = [
        {"id": str(c["id"]), "section_name": c["section_name"], "subject": c["subject"], "class_code": c["class_code"]}
        for c in classes
    ]

    session["active_class_id"] = str(selected_class_id)
    if cmeta:
        session["active_class_name"] = f"{cmeta['section_name']} ({cmeta['class_code']})"

    return render_template(
        "instructor_sessions.html",
        classes=classes_dropdown,
        selected_class_id=selected_class_id,
        selected_class_name=cmeta["section_name"] if cmeta else "",
        today=str(today_value),
        today_session=sess,
        all_sessions=all_sessions,
        active_class_id=selected_class_id,
        active_page="sessions",
    )

@app.route("/instructor/sessions/create", methods=["POST"])
def instructor_create_session():
    guard = instructor_required()
    if guard:
        return guard

    instructor_id = str(session.get("user_id") or "")
    class_id = (request.form.get("class_id") or "").strip()
    if not class_id:
        return redirect_with_msg("/instructor/sessions", "Missing class.")

    if not pg_instructor_in_class(instructor_id, class_id):
        return redirect_with_msg("/class-lists", "You are not assigned to this class.")

    start_date_str = (request.form.get("start_date") or "").strip()
    end_date_str = (request.form.get("end_date") or "").strip()
    all_day_str = (request.form.get("all_day") or "0").strip()
    
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except Exception:
        return redirect_with_msg("/instructor/sessions?class_id=" + class_id, "Invalid date range.")

    if end_date < start_date:
        return redirect_with_msg(
            "/instructor/sessions?class_id=" + class_id,
            "End Date must be after or equal to Start Date."
        )

    # Check if all-day mode is enabled
    is_all_day = all_day_str == "1"
    
    if is_all_day:
        # For all-day sessions, set times that span the entire day while passing validation
        present_start = _parse_time_hhmm("00:00")
        present_until = _parse_time_hhmm("23:57")
        late_until = _parse_time_hhmm("23:58")
        session_end = _parse_time_hhmm("23:59")
        late_start = _parse_time_hhmm("23:58")
    else:
        # Standard mode - parse times from form
        present_start_str = (request.form.get("present_start") or "").strip()
        present_until_str = (request.form.get("present_until") or "").strip()
        late_until_str = (request.form.get("late_until") or "").strip()
        session_end_str = (request.form.get("session_end") or "").strip()

        present_start = _parse_time_hhmm(present_start_str)
        present_until = _parse_time_hhmm(present_until_str)
        late_until = _parse_time_hhmm(late_until_str)
        session_end = _parse_time_hhmm(session_end_str)

        if not present_start or not present_until or not late_until or not session_end:
            return redirect_with_msg(
                "/instructor/sessions?class_id=" + class_id,
                "Please enter valid times."
            )

        if present_until <= present_start:
            return redirect_with_msg(
                "/instructor/sessions?class_id=" + class_id,
                "Present Until must be after Begin Time."
            )

        if late_until <= present_until:
            return redirect_with_msg(
                "/instructor/sessions?class_id=" + class_id,
                "Late Until must be after Present Until."
            )

        if session_end <= late_until:
            return redirect_with_msg(
                "/instructor/sessions?class_id=" + class_id,
                "End Time must be after Late Until."
            )

        late_start = _add_minutes(present_until, 1)

    pg_upsert_class_session(
        class_id=class_id,
        start_date=start_date,
        end_date=end_date,
        present_until=present_until,
        late_until=late_until,
        session_end=session_end,
        created_by=instructor_id,
        present_start=present_start,
        late_start=late_start,
        is_all_day=is_all_day,
    )

    return redirect_with_msg(
        "/instructor/sessions?class_id=" + class_id,
        "✅ Session date range and time window saved."
    )

@app.route("/api/instructor/session/<session_id>", methods=["GET"])
def api_get_session(session_id):
    """JSON API endpoint to get session details"""
    instructor_id = session.get("user_id")
    if not instructor_id or session.get("role") != "instructor":
        return {"error": "Unauthorized"}, 403

    instructor_id = str(instructor_id)
    session_id = (session_id or "").strip()
    
    if not session_id:
        return {"error": "Session ID required"}, 400
    
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 
                  cs.id,
                  cs.class_id,
                  cs.start_date,
                  cs.end_date,
                  cs.present_start,
                  cs.present_until,
                  cs.late_start,
                  cs.late_until,
                  cs.session_end,
                  COALESCE(cs.is_all_day, FALSE) AS is_all_day
                FROM class_sessions cs
                WHERE cs.id = %s
                  AND cs.class_id IN (
                    SELECT class_id FROM instructor_classes 
                    WHERE instructor_id = %s
                  )
                """,
                (session_id, instructor_id),
            )
            
            row = cur.fetchone()
            if not row:
                return {"error": "Session not found"}, 404
            
            sess_id, class_id, start_date, end_date, present_start, present_until, late_start, late_until, session_end, is_all_day = row
            
            return {
                "id": str(sess_id),
                "start_date": str(start_date),
                "end_date": str(end_date),
                "present_start": str(present_start) if present_start else None,
                "present_until": str(present_until) if present_until else None,
                "late_start": str(late_start) if late_start else None,
                "late_until": str(late_until) if late_until else None,
                "session_end": str(session_end) if session_end else None,
                "is_all_day": bool(is_all_day)
            }
    except Exception as e:
        print(f"API Error: {e}")
        return {"error": str(e)}, 500

@app.route("/instructor/sessions/<session_id>/delete", methods=["POST"])
def instructor_delete_session(session_id):
    """Delete a session"""
    guard = instructor_required()
    if guard:
        return guard
    
    instructor_id = str(session.get("user_id") or "")
    session_id = (session_id or "").strip()
    class_id = (request.form.get("class_id") or "").strip()
    
    if not session_id or not class_id:
        return redirect_with_msg("/instructor/sessions", "Invalid request")
    
    if not pg_instructor_in_class(instructor_id, class_id):
        return redirect_with_msg("/class-lists", "You are not assigned to this class.")
    
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            # Verify the session belongs to this instructor's class
            cur.execute(
                "SELECT id FROM class_sessions WHERE id = %s AND class_id = %s",
                (session_id, class_id)
            )
            if not cur.fetchone():
                return redirect_with_msg(f"/instructor/sessions?class_id={class_id}", "Session not found")
            
            # Delete the session
            cur.execute(
                "DELETE FROM class_sessions WHERE id = %s AND class_id = %s",
                (session_id, class_id)
            )
            conn.commit()
        
        return redirect_with_msg(
            f"/instructor/sessions?class_id={class_id}",
            "✅ Session deleted successfully"
        )
    except Exception as e:
        return redirect_with_msg(
            f"/instructor/sessions?class_id={class_id}",
            f"❌ Error deleting session: {str(e)}"
        )

@app.route("/instructor/sessions/all")
def instructor_view_all_sessions():
    guard = instructor_required()
    if guard:
        return guard

    instructor_id = str(session.get("user_id") or "")
    classes = pg_list_instructor_classes(instructor_id)
    if not classes:
        return redirect_with_msg("/class-lists", "No classes assigned to you yet.")

    selected_class_id = (request.args.get("class_id") or session.get("active_class_id") or str(classes[0]["id"])).strip()
    if not pg_instructor_in_class(instructor_id, selected_class_id):
        return redirect_with_msg("/class-lists", "You are not assigned to this class.")

    cmeta = pg_get_class_by_id(selected_class_id)
    all_sessions = pg_get_all_sessions_for_class(selected_class_id)

    classes_dropdown = [
        {"id": str(c["id"]), "section_name": c["section_name"], "subject": c["subject"], "class_code": c["class_code"]}
        for c in classes
    ]

    return render_template(
        "instructor_view_all_sessions.html",
        classes=classes_dropdown,
        selected_class_id=selected_class_id,
        selected_class_name=cmeta["section_name"] if cmeta else "",
        all_sessions=all_sessions,
        active_class_id=selected_class_id,
        active_page="sessions",
    )

@app.route("/instructor/quiz-creator")
@app.route("/instructor/quiz-creator/<class_id>")
def instructor_quiz_creator(class_id=None):
    guard = instructor_required()
    if guard:
        return guard

    if not class_id:
        class_id = request.args.get("class_id") or session.get("active_class_id") or pg_first_instructor_class_id(str(session.get("user_id") or ""))
        if not class_id:
            return redirect_with_msg("/class-lists", "Please select a class first.")

    instructor_id = str(session.get("user_id") or "")
    if not pg_instructor_in_class(instructor_id, str(class_id)):
        return redirect_with_msg("/class-lists", "You are not assigned to this class.")

    cmeta = pg_get_class_by_id(str(class_id))
    if not cmeta:
        return redirect_with_msg("/class-lists", "Class not found.")

    session["active_class_id"] = str(cmeta["id"])
    session["active_class_name"] = f"{cmeta['section_name']} ({cmeta['class_code']})"

    quiz_id = (request.args.get("id") or "").strip()

    return render_template(
        "instructor_quiz_creator.html",
        quiz_id=quiz_id,
        active_class_id=str(cmeta["id"]),
        active_page="quiz_creator",
    )

@app.route("/instructor/quiz-editor/<class_id>/<quiz_id>")
def instructor_quiz_editor(class_id, quiz_id):
    """Full quiz editor for editing existing quizzes"""
    try:
        guard = instructor_required()
        if guard:
            return guard

        instructor_id = str(session.get("user_id") or "")

        if not pg_instructor_in_class(instructor_id, str(class_id)):
            return redirect_with_msg("/class-lists", "You are not assigned to this class.")

        cmeta = pg_get_class_by_id(str(class_id))
        if not cmeta:
            return redirect_with_msg("/class-lists", "Class not found.")

        quiz = pg_get_quiz_by_id(str(quiz_id))
        if not quiz:
            return redirect_with_msg("/instructor/class-home/" + str(class_id), "Quiz not found.")

        if quiz.get("is_active", False):
            return redirect_with_msg("/instructor/class-home/" + str(class_id), "Cannot edit an active quiz. Please deactivate it first.")

        session["active_class_id"] = str(cmeta["id"])
        session["active_class_name"] = f"{cmeta['section_name']} ({cmeta['class_code']})"

        return render_template(
            "instructor_quiz_editor.html",
            quiz_id=str(quiz_id),
            class_id=str(class_id),
            active_class_id=str(cmeta["id"]),
            active_class_name=session["active_class_name"],
            active_page="quiz_editor",
        )
    except Exception as e:
        print(f"ERROR in quiz-editor route: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        return fail(f"Error loading quiz editor: {str(e)}", 500)

@app.route("/instructor/grades")
@app.route("/instructor/grades/<class_id>")
def instructor_grades(class_id=None):
    guard = instructor_required()
    if guard:
        return guard

    if not class_id:
        class_id = request.args.get("class_id") or session.get("active_class_id") or pg_first_instructor_class_id(str(session.get("user_id") or ""))
        if not class_id:
            return redirect_with_msg("/class-lists", "Please select a class first.")

    instructor_id = str(session.get("user_id") or "")
    if not pg_instructor_in_class(instructor_id, str(class_id)):
        return redirect_with_msg("/class-lists", "You are not assigned to this class.")

    cmeta = pg_get_class_by_id(str(class_id))
    if not cmeta:
        return redirect_with_msg("/class-lists", "Class not found.")

    session["active_class_id"] = str(cmeta["id"])
    session["active_class_name"] = f"{cmeta['section_name']} ({cmeta['class_code']})"

    quiz_options_raw = pg_list_quizzes_for_class(str(class_id), limit=500)
    quiz_options = [{"id": str(q["id"]), "title": q.get("title") or "Untitled"} for q in quiz_options_raw]

    selected_quiz_id = (request.args.get("quiz_id") or "").strip()
    grades = []
    error = ""

    if selected_quiz_id:
        qrow = pg_get_quiz_by_id(selected_quiz_id)
        if not qrow or str(qrow.get("class_id") or "") != str(class_id):
            error = "Quiz not found for this class."
        else:
            try:
                rows = pg_list_instructor_grades_for_quiz(instructor_id, selected_quiz_id, limit=5000)
                for r in rows:
                    score = int(r.get("score") or 0)
                    total_points = int(r.get("total_points") or 0)
                    percentage = int(round((score / total_points) * 100)) if total_points > 0 else 0
                    submitted_at = r.get("submitted_at")
                    grades.append(
                        {
                            "id": str(r.get("attempt_id") or ""),
                            "student_name": r.get("student_name") or "Unknown Student",
                            "quiz_title": r.get("quiz_title") or "Unknown Quiz",
                            "score": score,
                            "total_points": total_points,
                            "percentage": percentage,
                            "submitted_at": submitted_at.strftime("%Y-%m-%d %H:%M") if submitted_at else "-",
                        }
                    )
            except Exception as e:
                error = f"Failed to load grades: {str(e)}"

    return render_template(
        "instructor_grades.html",
        active_class_id=str(cmeta["id"]),
        quiz_options=quiz_options,
        selected_quiz_id=selected_quiz_id,
        grades=grades,
        error=error,
        active_page="grades",
    )

@app.route("/instructor/grades/csv")
@app.route("/instructor/grades/csv/<class_id>")
def instructor_grades_csv(class_id=None):
    guard = instructor_required()
    if guard:
        return guard

    if not class_id:
        class_id = request.args.get("class_id") or session.get("active_class_id")
        if not class_id:
            return redirect_with_msg("/class-lists", "Please select a class first.")

    instructor_id = str(session.get("user_id") or "")
    if not pg_instructor_in_class(instructor_id, str(class_id)):
        return redirect_with_msg("/class-lists", "You are not assigned to this class.")

    cmeta = pg_get_class_by_id(str(class_id))
    if not cmeta:
        return redirect_with_msg("/class-lists", "Class not found.")

    quiz_id = (request.args.get("quiz_id") or "").strip()
    if not quiz_id:
        return redirect_with_msg(url_for("instructor_grades", class_id=str(class_id)), "Please select a quiz first.")

    qrow = pg_get_quiz_by_id(quiz_id)
    if not qrow or str(qrow.get("class_id") or "") != str(class_id):
        return redirect_with_msg(url_for("instructor_grades", class_id=str(class_id)), "Quiz not found for this class.")

    rows = pg_list_instructor_grades_for_quiz(instructor_id, quiz_id, limit=5000)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Student Name", "Email", "Quiz Title", "Score", "Total Points", "Percentage", "Submitted Date"])

    for r in rows:
        score = int(r.get("score") or 0)
        total_points = int(r.get("total_points") or 0)
        percentage = int(round((score / total_points) * 100)) if total_points > 0 else 0
        submitted_at = r.get("submitted_at")
        writer.writerow([
            r.get("student_name") or "",
            r.get("email") or "",
            r.get("quiz_title") or "",
            score,
            total_points,
            percentage,
            submitted_at.strftime("%Y-%m-%d %H:%M") if submitted_at else "",
        ])

    resp = make_response(output.getvalue())
    filename = f"{cmeta['section_name']}_{cmeta['class_code']}_grades.csv".replace(" ", "_")
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp

@app.route("/instructor/monitor")
@app.route("/instructor/monitor/<class_id>")
@app.route("/instructor/monitor/<class_id>/<quiz_id>")
def instructor_monitor(class_id=None, quiz_id=None):
    guard = instructor_required()
    if guard:
        return guard

    if not class_id:
        class_id = request.args.get("class_id") or session.get("active_class_id") or pg_first_instructor_class_id(str(session.get("user_id") or ""))
        if not class_id:
            return redirect_with_msg("/class-lists", "Please select a class first.")

    instructor_id = str(session.get("user_id") or "")
    if not pg_instructor_in_class(instructor_id, str(class_id)):
        return redirect_with_msg("/class-lists", "You are not assigned to this class.")

    cmeta = pg_get_class_by_id(str(class_id))
    if not cmeta:
        return redirect_with_msg("/class-lists", "Class not found.")

    session["active_class_id"] = str(cmeta["id"])
    session["active_class_name"] = f"{cmeta['section_name']} ({cmeta['class_code']})"

    quiz_context = {}
    if quiz_id:
        quiz_context["selected_quiz_id"] = str(quiz_id)

    return render_template(
        "instructor_monitor.html",
        active_class_id=str(cmeta["id"]),
        active_page="monitor",
        **quiz_context
    )

@app.route("/api/instructor/quizzes-list", methods=["GET"])
def api_instructor_quizzes_list():
    guard = instructor_required()
    if guard:
        return fail("Not logged in", 401)

    class_id = (request.args.get("class_id") or session.get("active_class_id") or "").strip()
    if not class_id:
        return fail("Missing class_id", 400)

    instructor_id = str(session.get("user_id") or "")
    if not pg_instructor_in_class(instructor_id, class_id):
        return fail("You are not assigned to this class", 403)

    try:
        quizzes = pg_list_quizzes_for_class(str(class_id), limit=500)
        payload = []
        for q in quizzes:
            payload.append({
                "id": str(q.get("id") or ""),
                "title": str(q.get("title") or "Untitled Quiz"),
                "description": str(q.get("description") or ""),
            })
        return ok({"quizzes": payload}, "Quizzes loaded")
    except Exception as e:
        return fail(f"Error loading quizzes: {str(e)}", 500)

@app.route("/api/instructor/students-violations", methods=["GET"])
def api_instructor_students_violations():
    guard = instructor_required()
    if guard:
        return fail("Not logged in", 401)

    class_id = (request.args.get("class_id") or session.get("active_class_id") or "").strip()
    quiz_id = (request.args.get("quiz_id") or "").strip()
    if not class_id:
        return fail("Missing class_id", 400)

    instructor_id = str(session.get("user_id") or "")
    if not pg_instructor_in_class(instructor_id, class_id):
        return fail("You are not assigned to this class", 403)

    # CHANGED: Ensure violation table exists with correct schema before querying
    # CHANGED: violation table schema is verified once at app startup; avoid repeated checks here.

    try:
        with pg_conn() as conn, conn.cursor() as cur:
            quiz_filter = ""
            params = [str(instructor_id), str(class_id)]

            if quiz_id:
                quiz_filter = "AND qa.quiz_id = %s"
                params.append(str(quiz_id))

            # CHANGED: quiz_attempt_violations has NO user_id column.
            # User identity is resolved entirely through the quiz_attempts JOIN.
            # All qv.user_id references have been removed; only qa.user_id is used.
            cur.execute(
                f"""
                SELECT
                  qa.user_id  AS user_id,
                  COALESCE(u.full_name, 'Unknown') AS student_name,
                  COUNT(DISTINCT qv.id) AS violation_count
                FROM quiz_attempt_violations qv
                LEFT JOIN quiz_attempts qa
                  ON qa.attempt_id = qv.attempt_id
                LEFT JOIN users u
                  ON u.id = qa.user_id
                LEFT JOIN quizzes q
                  ON q.id = qa.quiz_id::uuid
                WHERE
                  qa.user_id IN (
                    SELECT cs.student_id
                    FROM class_students cs
                    JOIN class_instructors ci ON ci.class_id = cs.class_id
                    WHERE ci.instructor_id = %s
                      AND cs.class_id = %s
                  )
                  {quiz_filter}
                GROUP BY qa.user_id, u.full_name
                ORDER BY violation_count DESC, u.full_name ASC;
                """,
                params,
            )
            rows = cur.fetchall() or []
            return ok({"students": rows}, "Students with violations loaded")
    except Exception as e:
        print(f"❌ api_instructor_students_violations error: {str(e)}", flush=True)
        return fail(f"Error loading students: {str(e)}", 500)

@app.route("/api/instructor/violations", methods=["GET"])
def api_instructor_violations():
    guard = instructor_required()
    if guard:
        return fail("Not logged in", 401)

    class_id = (request.args.get("class_id") or session.get("active_class_id") or "").strip()
    quiz_id = (request.args.get("quiz_id") or "").strip()
    student_id = (request.args.get("student_id") or "").strip()
    if not class_id:
        return fail("Missing class_id", 400)

    instructor_id = str(session.get("user_id") or "")
    if not pg_instructor_in_class(instructor_id, class_id):
        return fail("You are not assigned to this class", 403)

    rows = pg_list_quiz_attempt_violations_for_class(instructor_id, class_id, quiz_id=quiz_id, student_id=student_id, limit=5000)
    return ok({"rows": rows}, "Violations loaded")

@app.route("/api/instructor/quizzes/<quiz_id>", methods=["GET"])
def api_instructor_quiz_get(quiz_id):
    guard = instructor_required()
    if guard:
        return fail("Not logged in", 401)

    qrow = pg_get_quiz_by_id(str(quiz_id))
    if not qrow:
        return fail("Quiz not found", 404)

    class_id = str(qrow.get("class_id") or "")
    instructor_id = str(session.get("user_id") or "")
    if not pg_instructor_in_class(instructor_id, class_id):
        return fail("You are not assigned to this class", 403)

    qjson = qrow.get("questions_json") or {}
    questions = qjson.get("questions") if isinstance(qjson, dict) else []
    if not isinstance(questions, list):
        questions = []
    image = qjson.get("image") if isinstance(qjson, dict) else None

    payload = {
        "id": str(qrow.get("id")),
        "classId": class_id,
        "title": qrow.get("title") or "",
        "description": qrow.get("description") or "",
        "totalScore": int(qrow.get("total_points") or 100),
        "timeLimitMinutes": int(qrow.get("time_limit_minutes") or 60),
        # NEW: Include attempts settings
        "attemptsType": qrow.get("attempts_type") or "unlimited",
        "allowedAttempts": qrow.get("attempts_limit"),
        "questions": questions,
    }
    if image is not None:
        payload["image"] = image
    return ok(payload, "Quiz loaded")

@app.route("/api/instructor/quizzes", methods=["POST"])
def api_instructor_quiz_create():
    guard = instructor_required()
    if guard:
        return fail("Not logged in", 401)
    if not _require_csrf_json():
        return fail("CSRF failed", 400)

    data = request.get_json(silent=True) or {}
    print(f"DEBUG api_instructor_quiz_create: Full payload keys: {list(data.keys())}", flush=True)
    
    class_id = str(data.get("classId") or "").strip()
    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()
    total_score = _safe_int(data.get("totalScore"), 100)
    time_limit = _safe_int(data.get("timeLimitMinutes"), 60)
    questions_raw = data.get("questions") or []
    # NEW: Extract attempts settings
    attempts_type = str(data.get("attemptsType") or "unlimited").strip()
    attempts_limit = data.get("allowedAttempts")
    if attempts_type not in ("unlimited", "limited"):
        return fail("Quiz attempts must be either unlimited or limited", 400)

    if attempts_type == "limited":
        if attempts_limit is None or str(attempts_limit).strip() == "":
            return fail("Number of attempts is required for limited attempts", 400)
        try:
            attempts_limit = int(attempts_limit)
            if attempts_limit <= 0:
                return fail("Number of attempts must be greater than 0", 400)
        except (ValueError, TypeError):
            return fail("Number of attempts must be a valid number", 400)
    else:
        attempts_limit = None

    print(f"DEBUG api_instructor_quiz_create: Received attempts_type={attempts_type}, attempts_limit={attempts_limit}", flush=True)

    if not class_id:
        return fail("Missing classId", 400)
    if not title:
        return fail("Missing title", 400)
    if total_score <= 0:
        return fail("totalScore must be greater than 0", 400)
    if time_limit <= 0:
        return fail("timeLimitMinutes must be greater than 0", 400)

    instructor_id = str(session.get("user_id") or "")
    if not pg_instructor_in_class(instructor_id, class_id):
        return fail("You are not assigned to this class", 403)

    questions, err = _normalize_quiz_questions(questions_raw)
    if err:
        return fail(err, 400)

    validation_error = _validate_instructor_quiz_payload(questions)
    if validation_error:
        return fail(validation_error, 400)

    question_total = sum(int(q.get("points") or 0) for q in questions)
    if question_total != total_score:
        return fail(
            f"Total question points must equal Total Score. Current question total is {question_total}, but Total Score is {total_score}.",
            400,
        )

    payload = {"questions": questions}

    try:
        quiz_id = pg_create_quiz(
            class_id=class_id,
            created_by=instructor_id,
            title=title,
            description=description,
            total_points=total_score,
            questions=payload,
            time_limit_minutes=time_limit,
            # NEW: Pass attempts settings
            attempts_type=attempts_type,
            attempts_limit=attempts_limit,
        )
    except Exception as e:
        return fail(f"DB error: {str(e)}", 500)

    return ok({"id": quiz_id}, "Quiz created")

@app.route("/api/instructor/quizzes/<quiz_id>", methods=["PUT"])
def api_instructor_quiz_update(quiz_id):
    guard = instructor_required()
    if guard:
        return fail("Not logged in", 401)
    if not _require_csrf_json():
        return fail("CSRF failed", 400)

    data = request.get_json(silent=True) or {}
    class_id = str(data.get("classId") or "").strip()
    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()
    total_score = _safe_int(data.get("totalScore"), 100)
    time_limit = _safe_int(data.get("timeLimitMinutes"), 60)
    questions_raw = data.get("questions") or []
    # NEW: Extract attempts settings
    attempts_type = str(data.get("attemptsType") or "unlimited").strip()
    attempts_limit = data.get("allowedAttempts")

    if not class_id:
        return fail("Missing classId", 400)
    if not title:
        return fail("Missing title", 400)
    if total_score <= 0:
        return fail("totalScore must be greater than 0", 400)
    if time_limit <= 0:
        return fail("timeLimitMinutes must be greater than 0", 400)

    if attempts_type not in ("unlimited", "limited"):
        return fail("Quiz attempts must be either unlimited or limited", 400)

    if attempts_type == "limited":
        if attempts_limit is None or str(attempts_limit).strip() == "":
            return fail("Number of attempts is required for limited attempts", 400)
        try:
            attempts_limit = int(attempts_limit)
            if attempts_limit <= 0:
                return fail("Number of attempts must be greater than 0", 400)
        except (ValueError, TypeError):
            return fail("Number of attempts must be a valid number", 400)
    else:
        attempts_limit = None

    instructor_id = str(session.get("user_id") or "")
    old_row = pg_get_quiz_by_id(str(quiz_id))
    if not old_row:
        return fail("Quiz not found", 404)

    if not pg_instructor_in_class(instructor_id, class_id):
        return fail("You are not assigned to this class", 403)

    questions, err = _normalize_quiz_questions(questions_raw)
    if err:
        return fail(err, 400)

    validation_error = _validate_instructor_quiz_payload(questions)
    if validation_error:
        return fail(validation_error, 400)

    question_total = sum(int(q.get("points") or 0) for q in questions)
    if question_total != total_score:
        return fail(
            f"Total question points must equal Total Score. Current question total is {question_total}, but Total Score is {total_score}.",
            400,
        )

    payload = {"questions": questions}

    try:
        updated = pg_update_quiz(
            quiz_id=str(quiz_id),
            class_id=class_id,
            title=title,
            description=description,
            total_points=total_score,
            questions=payload,
            updated_by=instructor_id,
            time_limit_minutes=time_limit,
            # NEW: Pass attempts settings
            attempts_type=attempts_type,
            attempts_limit=attempts_limit,
        )
        if not updated:
            return fail("Quiz update failed", 500)
    except Exception as e:
        return fail(f"DB error: {str(e)}", 500)

    return ok({"id": str(quiz_id)}, "Quiz updated")

@app.route("/api/instructor/quizzes/<quiz_id>/toggle-active", methods=["POST"])
def api_instructor_quiz_toggle_active(quiz_id):
    guard = instructor_required()
    if guard:
        return fail("Not logged in", 401)
    if not _require_csrf_json():
        return fail("CSRF failed", 400)

    instructor_id = str(session.get("user_id") or "")

    quiz = pg_get_quiz_by_id(str(quiz_id))
    if not quiz:
        return fail("Quiz not found", 404)

    class_id = str(quiz.get("class_id") or "")
    if not pg_instructor_in_class(instructor_id, class_id):
        return fail("You are not assigned to this class", 403)

    current_active = bool(quiz.get("is_active", False))
    new_active = not current_active

    try:
        updated = pg_toggle_quiz_activation(str(quiz_id), new_active)
        if not updated:
            return fail("Failed to toggle quiz activation", 500)
    except Exception as e:
        return fail(f"DB error: {str(e)}", 500)

    return ok({
        "id": str(quiz_id),
        "is_active": new_active,
        "status": "Activated" if new_active else "Deactivated"
    }, "Quiz activation toggled")

@app.route("/api/instructor/quizzes/<quiz_id>/edit", methods=["POST"])
def api_instructor_quiz_edit(quiz_id):
    guard = instructor_required()
    if guard:
        return fail("Not logged in", 401)
    if not _require_csrf_json():
        return fail("CSRF failed", 400)

    payload = request.get_json(silent=True) or {}
    print(f"DEBUG api_instructor_quiz_edit: quiz_id={quiz_id}, payload={payload}, csrf={request.headers.get('X-CSRF-Token')}", flush=True)

    instructor_id = str(session.get("user_id") or "")

    quiz = pg_get_quiz_by_id(str(quiz_id))
    if not quiz:
        return fail("Quiz not found", 404)

    class_id = str(quiz.get("class_id") or "")
    if not pg_instructor_in_class(instructor_id, class_id):
        return fail("You are not assigned to this class", 403)

    if quiz.get("is_active", False):
        return fail("Cannot edit an active quiz. Please deactivate it first.", 403)

    title = (payload.get("title") or "").strip()
    description = (payload.get("description") or "").strip() or ""
    total_score = int(payload.get("total_score") or payload.get("totalScore") or 0)

    if not title:
        return fail("Missing title", 400)

    if total_score <= 0:
        return fail("totalScore must be greater than 0", 400)
    attempts_type = str(payload.get("attempts_type") or payload.get("attemptsType") or "unlimited").strip() or "unlimited"
    attempts_limit = payload.get("attempts_limit") or payload.get("allowedAttempts")
    
    print(f"DEBUG api_instructor_quiz_edit: attempts_type={attempts_type}, attempts_limit={attempts_limit}", flush=True)
    
    if attempts_type not in ("unlimited", "limited"):
        return fail("Quiz attempts must be either unlimited or limited", 400)

    if attempts_type == "limited":
        if attempts_limit is None or str(attempts_limit).strip() == "":
            return fail("Number of attempts is required for limited attempts", 400)
        try:
            attempts_limit = int(attempts_limit)
            if attempts_limit <= 0:
                return fail("Number of attempts must be greater than 0", 400)
        except (ValueError, TypeError):
            return fail("Number of attempts must be a valid number", 400)
    else:
        attempts_limit = None
    
    print(f"DEBUG api_instructor_quiz_edit: Final attempts_type={attempts_type}, attempts_limit={attempts_limit}", flush=True)

    questions_data = payload.get("questions", [])
    question_count = len(questions_data) if questions_data else int(payload.get("question_count") or 0)

    try:
        questions_payload = {}
        if questions_data:
            normalized_questions, err = _normalize_quiz_questions(questions_data)
            if err:
                return fail(f"Question validation error: {err}", 400)

            validation_error = _validate_instructor_quiz_payload(normalized_questions)
            if validation_error:
                return fail(validation_error, 400)

            question_total = sum(int(q.get("points") or 0) for q in normalized_questions)
            if question_total != total_score:
                return fail(
                    f"Total question points must equal Total Score. Current question total is {question_total}, but Total Score is {total_score}.",
                    400,
                )

            questions_payload = {"questions": normalized_questions}

        pg_ensure_quiz_tables()
        with pg_conn() as conn, conn.cursor() as cur:
            if questions_payload:
                print(f"DEBUG: Updating quiz {quiz_id} with attempts_type={attempts_type}, attempts_limit={attempts_limit}", flush=True)
                cur.execute(
                    """
                    UPDATE quizzes
                       SET title=%s,
                           description=%s,
                           total_points=%s,
                           question_count=%s,
                           questions_json=%s,
                           attempts_type=%s,
                           attempts_limit=%s,
                           updated_at=now()
                     WHERE id=%s;
                    """,
                    (title, description, total_score, question_count, psycopg2.extras.Json(questions_payload), attempts_type, attempts_limit, str(quiz_id)),
                )
            else:
                print(f"DEBUG: Updating quiz {quiz_id} (no questions) with attempts_type={attempts_type}, attempts_limit={attempts_limit}", flush=True)
                cur.execute(
                    """
                    UPDATE quizzes
                       SET title=%s,
                           description=%s,
                           total_points=%s,
                           question_count=%s,
                           attempts_type=%s,
                           attempts_limit=%s,
                           updated_at=now()
                     WHERE id=%s;
                    """,
                    (title, description, total_score, question_count, attempts_type, attempts_limit, str(quiz_id)),
                )

            if cur.rowcount <= 0:
                return fail("Failed to update quiz", 500)
    except Exception as e:
        return fail(f"DB error: {str(e)}", 500)

    return ok({
        "id": str(quiz_id),
        "title": title,
        "description": description,
        "total_score": total_score,
        "question_count": question_count
    }, "Quiz updated successfully")

@app.route("/api/instructor/quizzes/<quiz_id>/delete", methods=["POST"])
def api_instructor_quiz_delete(quiz_id):
    guard = instructor_required()
    if guard:
        return fail("Not logged in", 401)
    if not _require_csrf_json():
        return fail("CSRF failed", 400)

    instructor_id = str(session.get("user_id") or "")

    quiz = pg_get_quiz_by_id(str(quiz_id))
    if not quiz:
        return fail("Quiz not found", 404)

    class_id = str(quiz.get("class_id") or "")
    if not pg_instructor_in_class(instructor_id, class_id):
        return fail("You are not assigned to this class", 403)

    # Delete the quiz
    try:
        pg_ensure_quiz_tables()
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM quizzes WHERE id=%s;", (str(quiz_id),))
            if cur.rowcount <= 0:
                return fail("Failed to delete quiz", 500)
    except Exception as e:
        return fail(f"DB error: {str(e)}", 500)

    return ok({"id": quiz_id}, "Quiz deleted successfully")

@app.route("/api/instructors", methods=["POST"])
def api_instructors_create():
    guard = admin_required()
    if guard:
        return jsonify({"success": False, "error": "Admin access only"}), 403

    data = request.get_json(silent=True) or {}

    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    email = (data.get("email") or "").strip()
    password = (data.get("password") or "").strip()

    if not first_name or not last_name or not email or not password:
        return jsonify({
            "success": False,
            "error": "All fields are required"
        }), 400

    try:
        # Create instructor in Firebase Auth first.
        fb_user = fb_auth.create_user(
            email=email,
            password=password,
            display_name=f"{first_name} {last_name}".strip(),
        )

        # Then create/update the matching PostgreSQL user profile.
        # IMPORTANT: This keeps the existing app design where users.firebase_uid
        # maps PostgreSQL profiles to Firebase Auth users.
        pg_create_or_update_user_profile(
            firebase_uid=fb_user.uid,
            first_name=first_name,
            last_name=last_name,
            email=email,
            role="instructor",
        )

        pg_user = pg_find_user_by_firebase_uid(fb_user.uid)

        return jsonify({
            "success": True,
            "message": "Instructor created successfully",
            "data": {
                "id": str(pg_user["id"]) if pg_user else None,
                "firebase_uid": fb_user.uid,
            },
        }), 201

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Failed to create instructor: {str(e)}"
        }), 500

@app.route("/api/instructors/<instructor_id>", methods=["PATCH"])
def api_instructors_update(instructor_id):
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

    try:
        # pg_update_user requires role as the last argument.
        updated = pg_update_user(
            instructor_id,
            first_name,
            last_name,
            email,
            "instructor",
        )

        if not updated:
            return jsonify({
                "success": False,
                "error": "Instructor not found or update failed"
            }), 404

        return jsonify({
            "success": True,
            "message": "Instructor updated successfully"
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Failed to update instructor: {str(e)}"
        }), 500

@app.route("/api/instructors/<instructor_id>", methods=["DELETE"])
def api_instructors_delete(instructor_id):
    guard = admin_required()
    if guard:
        return jsonify({"success": False, "error": "Admin access only"}), 403

    try:
        deleted = pg_delete_user(instructor_id)

        if not deleted:
            return jsonify({
                "success": False,
                "error": "Instructor not found or delete failed"
            }), 404

        return jsonify({
            "success": True,
            "message": "Instructor deleted successfully"
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Failed to delete instructor: {str(e)}"
        }), 500
