"""Student class, grade, and attendance page routes."""

from ._shared import _set_liveness_running, load_app_context


load_app_context(globals())


def _student_count_quiz_questions(questions_json):
    """
    Supports both quiz formats:
    1. [{"question": "..."}]
    2. {"questions": [{"question": "..."}]}
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


def _student_fix_quiz_question_counts(quizzes):
    fixed = []

    for quiz in quizzes or []:
        quiz_dict = dict(quiz)

        quiz_id = str(
            quiz_dict.get("id")
            or quiz_dict.get("quiz_id")
            or ""
        ).strip()

        question_count = None

        if (
            "questions_json" in quiz_dict
            or "questions" in quiz_dict
        ):
            question_count = _student_count_quiz_questions(
                quiz_dict.get("questions_json")
                or quiz_dict.get("questions")
            )

        if question_count is None and quiz_id:
            try:
                full_quiz = pg_get_quiz_by_id(quiz_id)

                if full_quiz:
                    question_count = _student_count_quiz_questions(
                        full_quiz.get("questions_json")
                        or full_quiz.get("questions")
                    )
            except Exception:
                question_count = 0

        if question_count is None:
            question_count = 0

        quiz_dict["question_count"] = int(question_count)
        quiz_dict["questionCount"] = int(question_count)

        fixed.append(quiz_dict)

    return fixed



@app.route("/class-lists")
def class_lists():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    session.pop("active_class_id", None)
    session.pop("active_class_name", None)

    user_id = str(session.get("user_id") or "")
    role = (session.get("role") or "").strip().lower()

    if role == "admin":
        return redirect(url_for("admin_dashboard"))

    if role == "instructor":
        classes = pg_list_instructor_classes(user_id)
        sections = [
            {
                "section": f"{c['section_name']} ({c['class_code']})",
                "subject": c["subject"],
                "href": url_for("instructor_class_home", class_id=str(c["id"])),
            }
            for c in classes
        ]
        empty_msg = "" if sections else "No classes assigned yet."
        return render_template(
            "class-lists.html",
            sections=sections,
            empty_msg=empty_msg,
            active_page="class_list",
            show_class_nav=False
        )

    guard = student_required()
    if guard:
        return guard

    classes = pg_list_student_classes(user_id)
    sections = [
        {
            "section": f"{c['section_name']} ({c['class_code']})",
            "subject": c["subject"],
            "href": url_for("stud_class_home", class_id=str(c["id"])),
        }
        for c in classes
    ]
    empty_msg = "" if sections else "No classes enrolled yet."
    return render_template(
        "class-lists.html",
        sections=sections,
        empty_msg=empty_msg,
        active_page="class_list",
        show_class_nav=False
    )

@app.route("/stud-class-home")
@app.route("/stud-class-home/<class_id>")
def stud_class_home(class_id=None):
    guard = student_required()
    if guard:
        return guard

    student_id = str(session.get("user_id") or "")

    if not class_id:
        class_id = session.get("active_class_id") or pg_first_student_class_id(student_id)
        if not class_id:
            return redirect_with_msg("/class-lists", "No classes enrolled yet.")

    if not pg_student_in_class(student_id, class_id):
        return redirect_with_msg("/class-lists", "You are not enrolled in this class.")

    cmeta = pg_get_class_by_id(class_id)
    if not cmeta:
        return redirect_with_msg("/class-lists", "Class not found.")

    session["active_class_id"] = str(cmeta["id"])
    session["active_class_name"] = f"{cmeta['section_name']} ({cmeta['class_code']})"

    sess = pg_get_active_session_for_date(str(cmeta["id"]), datetime.now().date())

    try:
        if sess:
            quiz_available, quiz_status = compute_quiz_availability(datetime.now(), sess)
        else:
            quiz_available, quiz_status = False, "no_session"

        quizzes = _build_quiz_cards_for_class(
            str(cmeta["id"]),
            quiz_available=quiz_available,
            quiz_status=quiz_status,
            is_instructor=False,
            student_id=student_id
        )

        quizzes = _student_fix_quiz_question_counts(quizzes)
    except Exception:
        quizzes = []
        quiz_available, quiz_status = False, "no_session"

    return render_template(
        "stud-class-home.html",
        quizzes=quizzes,
        today_session=sess,
        quiz_status=quiz_status,
        quiz_available=quiz_available,
        active_class_id=str(cmeta["id"]),
        active_class_name=f"{cmeta['section_name']} ({cmeta['class_code']})",
        active_page="class_home",
    )

@app.route("/stud-grades")
def stud_grades():
    guard = student_required()
    if guard:
        return guard

    user_id = str(session.get("user_id") or "")
    class_id = (session.get("active_class_id") or "").strip()

    if not class_id:
        return redirect_with_msg("/class-lists", "Please select your class first.")

    if not pg_student_in_class(user_id, class_id):
        return redirect_with_msg("/class-lists", "You are not enrolled in this class.")

    grades = pg_list_student_grades(user_id, class_id, limit=50)

    return render_template(
        "stud-grades.html",
        grades=grades,
        active_class_id=class_id,
    )

@app.route("/stud-attendance")
def stud_attendance():
    guard = student_required()
    if guard:
        return guard

    user_id = str(session.get("user_id") or "")
    class_id = (session.get("active_class_id") or "").strip()

    if not class_id:
        return redirect_with_msg("/class-lists", "Please select your class first.")

    if not pg_student_in_class(user_id, class_id):
        return redirect_with_msg("/class-lists", "You are not enrolled in this class.")

    summary, records = pg_list_student_attendance(user_id, class_id, limit=120)

    return render_template(
        "stud-attendance.html",
        summary=summary,
        records=records,
        active_class_id=class_id,
    )
