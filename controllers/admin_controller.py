"""Admin dashboard, user, class, and membership page/API routes."""

from ._shared import (
    _set_liveness_running,
    human_violation_label,
    load_app_context,
)

load_app_context(globals())

def pg_ensure_student_login_logs():
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS student_login_logs (
                    id bigserial PRIMARY KEY,
                    student_id uuid REFERENCES users(id) ON DELETE SET NULL,
                    student_name text,
                    email text,
                    ip_address text,
                    user_agent text,
                    login_at timestamptz NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_student_login_logs_login_at ON student_login_logs(login_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_student_login_logs_student_id ON student_login_logs(student_id);")
            conn.commit()
    except Exception as err:
        app.logger.warning("Failed to ensure student_login_logs table: %s", err)


def pg_list_student_login_logs(limit=30):
    pg_ensure_student_login_logs()
    with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, student_id, student_name, email, ip_address, user_agent,
                   login_at,
                   to_char(login_at AT TIME ZONE 'Asia/Manila', 'Mon DD, YYYY HH12:MI AM') AS login_time
            FROM student_login_logs
            ORDER BY login_at DESC
            LIMIT %s
            """,
            (int(limit),),
        )
        return cur.fetchall() or []

def pg_list_student_login_logs_for_range(start_date, end_date, newest_first=False):
    pg_ensure_student_login_logs()
    order_sql = "DESC" if newest_first else "ASC"
    with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT id, student_id, student_name, email, ip_address, user_agent,
                   login_at,
                   to_char(login_at AT TIME ZONE 'Asia/Manila', 'Mon DD, YYYY HH12:MI AM') AS login_time
            FROM student_login_logs
            WHERE (login_at AT TIME ZONE 'Asia/Manila')::date BETWEEN %s::date AND %s::date
            ORDER BY login_at {order_sql}, id {order_sql};
            """,
            (start_date, end_date),
        )
        return cur.fetchall() or []

@app.route("/admin/dashboard")
def admin_dashboard():
    guard = admin_required()
    if guard:
        return guard

    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM users WHERE role='student';")
        students = int((cur.fetchone() or {}).get("count", 0))

        cur.execute("SELECT COUNT(*) AS count FROM users WHERE role='instructor';")
        instructors = int((cur.fetchone() or {}).get("count", 0))

        cur.execute("SELECT COUNT(*) AS count FROM users WHERE role='admin';")
        admins = int((cur.fetchone() or {}).get("count", 0))

        cur.execute("SELECT COUNT(*) AS count FROM classes;")
        classes = int((cur.fetchone() or {}).get("count", 0))

        try:
            cur.execute("SELECT COUNT(*) AS count FROM quizzes;")
            quizzes = int((cur.fetchone() or {}).get("count", 0))
        except Exception:
            quizzes = 0

        # ==========================================================
        # ATTENDANCE ANALYTICS
        # Uses FULL roster logic instead of attendance_records only.
        # ==========================================================
        try:

            # Total enrolled roster students across all classes
            cur.execute("""
                SELECT COUNT(*) AS total_roster
                FROM class_students;
            """)
            roster_row = cur.fetchone() or {}
            total_roster = int(roster_row.get("total_roster") or 0)

            # Present / Late counts from saved attendance
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (
                        WHERE LOWER(COALESCE(status, '')) = 'present'
                    ) AS present_count,

                    COUNT(*) FILTER (
                        WHERE LOWER(COALESCE(status, '')) = 'late'
                    ) AS late_count

                FROM attendance_records;
            """)

            attendance_summary = cur.fetchone() or {}

        except Exception:
            total_roster = 0
            attendance_summary = {}

        attendance_present = int(
            attendance_summary.get("present_count") or 0
        )

        attendance_late = int(
            attendance_summary.get("late_count") or 0
        )

        attendance_absent = max(
            total_roster - attendance_present - attendance_late,
            0
        )

        attendance_total = total_roster

        attendance_rate = int(
            round(
                ((attendance_present + attendance_late) / attendance_total) * 100
            )
        ) if attendance_total else 0

        try:
            cur.execute(
                """
                SELECT
                    c.id AS class_id,
                    c.class_code,
                    c.section_name,
                    c.subject,
                    COUNT(ar.id) AS total_records,
                    COUNT(ar.id) FILTER (WHERE LOWER(COALESCE(ar.status, '')) = 'present') AS present_count,
                    COUNT(ar.id) FILTER (WHERE LOWER(COALESCE(ar.status, '')) = 'late') AS late_count,
                    COUNT(ar.id) FILTER (WHERE LOWER(COALESCE(ar.status, '')) = 'absent') AS absent_count
                FROM classes c
                LEFT JOIN attendance_records ar
                    ON ar.class_id = c.id
                GROUP BY c.id, c.class_code, c.section_name, c.subject
                ORDER BY c.class_code ASC, c.section_name ASC;
                """
            )
            attendance_by_class = cur.fetchall() or []
        except Exception:
            attendance_by_class = []

    return render_template(
        "admin_dashboard.html",
        students=students,
        instructors=instructors,
        admins=admins,
        classes=classes,
        quizzes=quizzes,
        attendance_total=attendance_total,
        attendance_present=attendance_present,
        attendance_late=attendance_late,
        attendance_absent=attendance_absent,
        attendance_rate=attendance_rate,
        attendance_by_class=attendance_by_class,
    )

@app.route("/admin/users")
def admin_users():
    guard = admin_required()
    if guard:
        return guard

    users = pg_list_all_users(limit=2000)
    return render_template("admin_users.html", users=users)


@app.route("/api/admin/student-login-logs")
def api_admin_student_login_logs():
    guard = admin_required()
    if guard:
        return jsonify({"ok": False, "message": "Admin access only"}), 403

    try:
        limit = min(max(int(request.args.get("limit", 30)), 1), 100)
    except Exception:
        limit = 30

    start_date = (request.args.get("start_date") or "").strip()
    end_date = (request.args.get("end_date") or "").strip()

    try:
        if start_date or end_date:
            from datetime import datetime
            if not start_date:
                start_date = end_date
            if not end_date:
                end_date = start_date
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
            if start_dt > end_dt:
                return jsonify({"ok": False, "message": "Start date must be before or equal to end date."}), 400
            rows = pg_list_student_login_logs_for_range(start_dt.isoformat(), end_dt.isoformat(), newest_first=True)
        else:
            rows = pg_list_student_login_logs(limit=limit)
        return jsonify({"ok": True, "logs": [dict(row) for row in rows]})
    except ValueError:
        return jsonify({"ok": False, "message": "Invalid date filter."}), 400
    except Exception as err:
        app.logger.error("Failed to load student login logs: %s", err, exc_info=True)
        return jsonify({"ok": False, "message": "Failed to load login logs"}), 500

@app.route("/admin/student-login-logs/export-pdf")
def admin_student_login_logs_export_pdf():
    guard = admin_required()
    if guard:
        return guard

    start_date = (request.args.get("start_date") or "").strip()
    end_date = (request.args.get("end_date") or "").strip()
    if not start_date and not end_date:
        return redirect_with_msg("/admin/dashboard", "Please select a start date or end date before exporting login logs.")
    if not start_date:
        start_date = end_date
    if not end_date:
        end_date = start_date

    try:
        from datetime import datetime
        from io import BytesIO

        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
    except Exception:
        return redirect_with_msg("/admin/dashboard", "Invalid login log export date. Please use YYYY-MM-DD.")

    if start_dt > end_dt:
        return redirect_with_msg("/admin/dashboard", "Start date must be before or equal to end date.")

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except Exception as err:
        app.logger.error("ReportLab unavailable for student login PDF export: %s", err, exc_info=True)
        return redirect_with_msg("/admin/dashboard", "PDF export is unavailable because the PDF library is not installed.")

    rows = pg_list_student_login_logs_for_range(start_dt.isoformat(), end_dt.isoformat())
    generated_at = app_now().strftime("%b %d, %Y %I:%M %p")
    date_range_label = start_dt.strftime("%b %d, %Y") if start_dt == end_dt else f"{start_dt.strftime('%b %d, %Y')} - {end_dt.strftime('%b %d, %Y')}"

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=0.35 * inch,
        leftMargin=0.35 * inch,
        topMargin=0.35 * inch,
        bottomMargin=0.35 * inch,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Student Login Logs Report", styles["Title"]),
        Paragraph(f"Selected date range: {date_range_label}", styles["Normal"]),
        Paragraph(f"Generated: {generated_at}", styles["Normal"]),
        Paragraph(f"Total login records: {len(rows)}", styles["Normal"]),
        Spacer(1, 0.18 * inch),
    ]

    if rows:
        table_data = [["Student Name", "Email", "Login Timestamp", "IP Address"]]
        for row in rows:
            table_data.append([
                str(row.get("student_name") or "Unknown Student"),
                str(row.get("email") or "No email"),
                str(row.get("login_time") or ""),
                str(row.get("ip_address") or "Unknown IP"),
            ])
        table = Table(table_data, colWidths=[2.15 * inch, 2.55 * inch, 2.0 * inch, 1.55 * inch], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("No student login records found for the selected date range.", styles["Heading3"]))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    filename = f"student-login-logs-{start_dt.isoformat()}-to-{end_dt.isoformat()}.pdf"
    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

@app.route("/api/admin/exam-entry-logs")
def api_admin_exam_entry_logs():
    guard = admin_required()
    if guard:
        return jsonify({"ok": False, "message": "Admin access only"}), 403

    try:
        limit = min(max(int(request.args.get("limit", 30)), 1), 100)
    except Exception:
        limit = 30

    try:
        rows = pg_list_exam_entry_logs(limit=limit)
        summary = pg_exam_entry_summary_today()
        return jsonify({"ok": True, "logs": [dict(row) for row in rows], "summary": summary})
    except Exception as err:
        app.logger.error("Failed to load exam entry logs: %s", err, exc_info=True)
        return jsonify({"ok": False, "message": "Failed to load exam entry logs"}), 500
@app.route("/admin/students")
def admin_students():
    guard = admin_required()
    if guard:
        return guard

    with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT DISTINCT u.id, u.first_name, u.last_name, u.email, u.role,
                   STRING_AGG(c.class_code || ' (' || c.section_name || ')', ', ' ORDER BY c.class_code) as classes
            FROM users u
            LEFT JOIN class_students cs ON u.id = cs.student_id
            LEFT JOIN classes c ON cs.class_id = c.id
            WHERE u.role='student'
            GROUP BY u.id, u.first_name, u.last_name, u.email, u.role
            ORDER BY u.first_name, u.last_name;
        """)
        students = cur.fetchall() or []

    return render_template("admin_students.html", students=students)

@app.route("/admin/instructors")
def admin_instructors():
    guard = admin_required()
    if guard:
        return guard

    with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT u.*,
                   (SELECT COUNT(DISTINCT ci.class_id) 
                    FROM class_instructors ci 
                    WHERE ci.instructor_id = u.id) as class_count,
                   (SELECT COUNT(*) 
                    FROM quizzes q 
                    WHERE q.created_by = u.id) as quiz_count
            FROM users u
            WHERE u.role='instructor'
            ORDER BY u.first_name, u.last_name;
        """)
        instructors = cur.fetchall() or []

    return render_template("admin_instructors.html", instructors=instructors)

@app.route("/admin/instructors/create", methods=["POST"])
def admin_create_instructor():
    guard = admin_required()
    if guard:
        return guard

    first_name = (request.form.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    password = (request.form.get("password") or "").strip()

    if not first_name or not last_name or not email or not password:
        return redirect_with_msg("/admin/instructors", "All fields are required.")

    try:
        # Create Firebase user
        fb_user = fb_auth.create_user(email=email, password=password)
        firebase_uid = fb_user.uid

        # Create in database
        user_id = str(uuid.uuid4())
        full_name = f"{first_name} {last_name}"
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (id, firebase_uid, first_name, last_name, full_name, email, role, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (user_id, firebase_uid, first_name, last_name, full_name, email, "instructor")
            )
            conn.commit()

        return redirect_with_msg("/admin/instructors", f"Instructor {first_name} {last_name} created successfully.")
    except Exception as err:
        return redirect_with_msg("/admin/instructors", f"Error creating instructor: {str(err)}")

@app.route("/admin/instructors/edit/<instructor_id>", methods=["POST"])
def admin_edit_instructor(instructor_id):
    guard = admin_required()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    email = (data.get("email") or "").strip().lower()

    if not first_name or not last_name or not email:
        return fail("Missing required fields", 400)

    try:
        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id FROM users WHERE email = %s AND id != %s",
                (email, instructor_id)
            )
            if cur.fetchone():
                return fail("Email already in use", 400)

            full_name = f"{first_name} {last_name}"
            cur.execute("""
                UPDATE users 
                SET first_name = %s, last_name = %s, full_name = %s, email = %s
                WHERE id = %s
            """, (first_name, last_name, full_name, email, instructor_id))
            conn.commit()

        return ok({"message": "Instructor updated successfully"})
    except Exception as err:
        print(f"Error updating instructor: {str(err)}")
        return fail(f"Error updating instructor: {str(err)}", 500)

@app.route("/api/admin/instructor-classes/<instructor_id>", methods=["GET"])
def api_instructor_classes(instructor_id):
    guard = admin_required()
    if guard:
        return guard

    try:
        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT c.id, c.section_name as name, c.class_code as code,
                       COALESCE((SELECT COUNT(*) 
                                 FROM quizzes q 
                                 WHERE q.class_id = c.id AND q.created_by = %s::uuid), 0) as quiz_count
                FROM classes c
                INNER JOIN class_instructors ci ON c.id = ci.class_id
                WHERE ci.instructor_id = %s::uuid
                ORDER BY c.section_name;
            """, (instructor_id, instructor_id))
            classes = cur.fetchall() or []

        return ok({"classes": classes}, "Classes fetched successfully")
    except Exception as err:
        print(f"DEBUG: Error fetching classes: {str(err)}")
        import traceback
        traceback.print_exc()
        return fail(f"Error fetching classes: {str(err)}", 500)

@app.route("/api/admin/all-classes", methods=["GET"])
def api_all_classes():
    guard = admin_required()
    if guard:
        return guard

    try:
        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT c.id, c.class_code, c.section_name, c.subject
                FROM classes c
                ORDER BY c.class_code ASC;
            """)
            classes = cur.fetchall() or []

        return ok({"classes": classes}, "Classes fetched successfully")
    except Exception as err:
        print(f"Error fetching all classes: {str(err)}")
        return fail(f"Error fetching classes: {str(err)}", 500)

@app.route("/api/admin/student-add-classes/<student_id>", methods=["POST"])
def api_student_add_classes(student_id):
    guard = admin_required()
    if guard:
        return guard

    try:
        data = request.get_json(silent=True) or {}
        class_ids = data.get("class_ids", [])

        if not isinstance(class_ids, list):
            return fail("Invalid class_ids format", 400)

        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT class_id FROM class_students
                WHERE student_id = %s::uuid;
            """, (student_id,))
            existing_classes = set([row['class_id'] for row in cur.fetchall()])

            class_ids_to_add = [cid for cid in class_ids if cid not in existing_classes]

            for class_id in class_ids_to_add:
                cur.execute("""
                    INSERT INTO class_students (student_id, class_id, enrolled_at)
                    VALUES (%s::uuid, %s::uuid, %s)
                    ON CONFLICT DO NOTHING;
                """, (student_id, class_id, datetime.now()))

            conn.commit()

        return ok({"message": "Classes added successfully", "classes_added": len(class_ids_to_add)})
    except Exception as err:
        print(f"Error adding classes to student: {str(err)}")
        import traceback
        traceback.print_exc()
        return fail(f"Error adding classes: {str(err)}", 500)

@app.route("/api/admin/student-enrolled-classes/<student_id>", methods=["GET"])
def api_student_enrolled_classes(student_id):
    guard = admin_required()
    if guard:
        return guard

    try:
        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT c.id, c.class_code, c.section_name, c.subject
                FROM class_students cs
                JOIN classes c ON cs.class_id = c.id
                WHERE cs.student_id = %s::uuid
                ORDER BY c.class_code ASC;
            """, (student_id,))
            classes = cur.fetchall() or []

        return ok({"classes": classes}, "Enrolled classes fetched successfully")
    except Exception as err:
        print(f"Error fetching enrolled classes: {str(err)}")
        return fail(f"Error fetching classes: {str(err)}", 500)

@app.route("/api/admin/student-remove-classes/<student_id>", methods=["POST"])
def api_student_remove_classes(student_id):
    guard = admin_required()
    if guard:
        return guard

    try:
        data = request.get_json(silent=True) or {}
        class_ids = data.get("class_ids", [])

        if not isinstance(class_ids, list):
            return fail("Invalid class_ids format", 400)

        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            for class_id in class_ids:
                cur.execute("""
                    DELETE FROM class_students
                    WHERE student_id = %s::uuid AND class_id = %s::uuid;
                """, (student_id, class_id))

            conn.commit()

        return ok({"message": "Classes removed successfully", "classes_removed": len(class_ids)})
    except Exception as err:
        print(f"Error removing classes from student: {str(err)}")
        import traceback
        traceback.print_exc()
        return fail(f"Error removing classes: {str(err)}", 500)

@app.route("/admin/users/update/<user_id>", methods=["POST"])
def admin_update_user(user_id):
    guard = admin_required()
    if guard:
        return guard
    _require_csrf_form()

    first_name = (request.form.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    role = (request.form.get("role") or "").strip().lower()

    if not first_name or not last_name or not email:
        return redirect_with_msg("/admin/users", "Please complete all required user fields.")

    if role not in ("student", "instructor", "admin"):
        return redirect_with_msg("/admin/users", "Invalid user role.")

    me = str(session.get("user_id") or "")
    if str(user_id) == me and role != "admin":
        return redirect_with_msg("/admin/users", "You cannot remove your own admin role.")

    updated = pg_update_user(str(user_id), first_name, last_name, email, role)
    if not updated:
        return redirect_with_msg("/admin/users", "User update failed or user not found.")

    return redirect_with_msg("/admin/users", "User updated successfully.")

@app.route("/admin/users/password/<user_id>", methods=["POST"])
def admin_reset_user_password(user_id):
    guard = admin_required()
    if guard:
        return guard
    _require_csrf_form()

    password = request.form.get("password") or ""
    confirm_password = request.form.get("confirm_password") or ""

    if len(password) < 8:
        return redirect_with_msg("/admin/users", "Password must be at least 8 characters.")

    if password != confirm_password:
        return redirect_with_msg("/admin/users", "Password confirmation does not match.")

    row = pg_find_user_by_pg_id(str(user_id))
    if not row:
        return redirect_with_msg("/admin/users", "User not found.")

    firebase_uid = (row.get("firebase_uid") or "").strip()
    email = (row.get("email") or "").strip().lower()

    try:
        if not firebase_uid and email:
            firebase_user = fb_auth.get_user_by_email(email)
            firebase_uid = str(firebase_user.uid or "")
            if firebase_uid:
                with pg_conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET firebase_uid = %s WHERE id = %s;",
                        (firebase_uid, str(user_id)),
                    )
                    conn.commit()

        if not firebase_uid:
            return redirect_with_msg("/admin/users", "This user has no linked Firebase account.")

        fb_auth.update_user(firebase_uid, password=password)
        return redirect_with_msg("/admin/users", f"Password updated for {email or 'selected user'}.")
    except Exception as err:
        app.logger.error("Admin password reset failed: %s: %s", type(err).__name__, err, exc_info=True)
        return redirect_with_msg("/admin/users", f"Password reset failed: {str(err)}")

@app.route("/admin/users/send-reset/<user_id>", methods=["POST"])
def admin_send_user_password_reset(user_id):
    guard = admin_required()
    if guard:
        return guard
    _require_csrf_form()

    row = pg_find_user_by_pg_id(str(user_id))
    if not row:
        return redirect_with_msg("/admin/users", "User not found.")

    email = (row.get("email") or "").strip().lower()
    if not email:
        return redirect_with_msg("/admin/users", "This user has no email address.")

    try:
        method = send_classiface_password_reset_email(email)
        source = "Gmail" if method == "smtp" else "Firebase"
        return redirect_with_msg("/admin/users", f"Password reset email sent to {email} via {source}.")
    except Exception as err:
        app.logger.error(
            "Admin reset email failed for %s: %s: %s",
            _mask_email(email),
            type(err).__name__,
            err,
            exc_info=True,
        )
        return redirect_with_msg(
            "/admin/users",
            "Password reset email failed. Use Password to set it manually.",
        )

@app.route("/admin/students/edit/<student_id>", methods=["POST"])
def admin_edit_student(student_id):
    guard = admin_required()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    email = (data.get("email") or "").strip().lower()

    if not first_name or not last_name or not email:
        return fail("Missing required fields", 400)

    try:
        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id FROM users WHERE email = %s AND id != %s",
                (email, student_id)
            )
            if cur.fetchone():
                return fail("Email already in use", 400)

            full_name = f"{first_name} {last_name}"
            cur.execute("""
                UPDATE users 
                SET first_name = %s, last_name = %s, full_name = %s, email = %s
                WHERE id = %s
            """, (first_name, last_name, full_name, email, student_id))
            conn.commit()

        return ok({"message": "Student updated successfully"})
    except Exception as err:
        print(f"Error updating student: {str(err)}")
        return fail(f"Error updating student: {str(err)}", 500)

@app.route("/admin/users/delete/<user_id>", methods=["POST"])
def admin_delete_user(user_id):
    guard = admin_required()
    if guard:
        return guard
    _require_csrf_form()

    me = str(session.get("user_id") or "")
    if str(user_id) == me:
        return redirect_with_msg("/admin/users", "You cannot delete your own account.")

    row = pg_find_user_by_pg_id(str(user_id))
    if not row:
        return redirect_with_msg("/admin/users", "User not found.")

    if str(user_id) == me:
        return redirect_with_msg("/admin/users", "You cannot delete your own account.")

    deleted = pg_delete_user(str(user_id))
    if not deleted:
        return redirect_with_msg("/admin/users", "Delete failed.")

    user_role = row.get("role", "student") if isinstance(row, dict) else getattr(row, "role", "student")
    if user_role == "instructor":
        return redirect_with_msg("/admin/instructors", "Instructor deleted successfully.")
    elif user_role == "student":
        return redirect_with_msg("/admin/students", "Student deleted successfully.")
    else:
        return redirect_with_msg("/admin/users", "User deleted successfully.")

@app.route("/admin/settings")
def admin_settings():
    guard = admin_required()
    if guard:
        return guard

    with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE role='admin' ORDER BY first_name, last_name;")
        admins = cur.fetchall() or []

    return render_template("admin_settings.html", admins=admins)

@app.route("/admin/settings/create-admin", methods=["POST"])
def admin_create_admin():
    guard = admin_required()
    if guard:
        return guard
    _require_csrf_form()

    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()

    if not all([first_name, last_name, email, password]):
        return redirect_with_msg("/admin/settings", "All fields are required.")

    try:
        user = fb_auth.create_user(email=email, password=password)
        firebase_uid = user.uid

        full_name = f"{first_name} {last_name}"

        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (firebase_uid, first_name, last_name, full_name, email, role)
                VALUES (%s, %s, %s, %s, %s, %s);
                """,
                (firebase_uid, first_name, last_name, full_name, email, "admin"),
            )
            conn.commit()

        return redirect_with_msg("/admin/settings", f"Admin {first_name} {last_name} created successfully.")

    except Exception as err:
        return redirect_with_msg("/admin/settings", f"Error creating admin: {str(err)}")

@app.route("/admin/classes")
def admin_classes():
    guard = admin_required()
    if guard:
        return guard

    classes = pg_list_all_classes(limit=2000)
    return render_template("admin_classes.html", classes=classes)

@app.route("/admin/classes/create", methods=["POST"])
def admin_create_class():
    guard = admin_required()
    if guard:
        return guard
    _require_csrf_form()

    class_code = (request.form.get("class_code") or "").strip()
    section_name = (request.form.get("section_name") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    description = (request.form.get("description") or "").strip()

    if not class_code or not section_name or not subject:
        return redirect_with_msg("/admin/classes", "Please complete all required class fields.")

    try:
        pg_create_class(class_code, section_name, subject, description)
    except Exception as e:
        return redirect_with_msg("/admin/classes", f"Failed to create class: {str(e)}")

    return redirect_with_msg("/admin/classes", "Class created successfully.")

@app.route("/admin/classes/update/<class_id>", methods=["POST"])
def admin_update_class(class_id):
    guard = admin_required()
    if guard:
        return guard
    _require_csrf_form()

    class_code = (request.form.get("class_code") or "").strip()
    section_name = (request.form.get("section_name") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    description = (request.form.get("description") or "").strip()

    if not class_code or not section_name or not subject:
        return redirect_with_msg("/admin/classes", "Please complete all required class fields.")

    updated = pg_update_class(str(class_id), class_code, section_name, subject, description)
    if not updated:
        return redirect_with_msg("/admin/classes", "Class update failed or class not found.")

    return redirect_with_msg("/admin/classes", "Class updated successfully.")

@app.route("/admin/classes/delete/<class_id>", methods=["POST"])
def admin_delete_class(class_id):
    guard = admin_required()
    if guard:
        return guard
    _require_csrf_form()

    deleted = pg_delete_class(str(class_id))
    if not deleted:
        return redirect_with_msg("/admin/classes", "Class delete failed.")

    return redirect_with_msg("/admin/classes", "Class deleted successfully.")

@app.route("/admin/classes/<class_id>/students")
def admin_class_students(class_id):
    guard = admin_required()
    if guard:
        return guard

    cmeta = pg_get_class_by_id(str(class_id))
    if not cmeta:
        return redirect_with_msg("/admin/classes", "Class not found.")

    students = pg_list_all_users(role="student", limit=5000)
    assigned = pg_list_students_in_class(str(class_id), limit=5000)

    return render_template(
        "admin_class_students.html",
        class_id=str(class_id),
        class_meta=cmeta,
        students=students,
        assigned=assigned,
    )

@app.route("/admin/classes/<class_id>/students/add", methods=["POST"])
def admin_add_student(class_id):
    guard = admin_required()
    if guard:
        return guard
    _require_csrf_form()

    student_id = (request.form.get("student_id") or "").strip()
    if not student_id:
        return redirect(request.referrer or url_for("admin_class_students", class_id=class_id))

    try:
        pg_assign_student_to_class(str(student_id), str(class_id))
    except Exception as e:
        return redirect_with_msg(
            request.referrer or url_for("admin_class_students", class_id=class_id),
            f"Failed to assign student: {str(e)}"
        )

    return redirect(request.referrer or url_for("admin_class_students", class_id=class_id))

@app.route("/admin/classes/<class_id>/students/remove/<student_id>", methods=["POST"])
def admin_remove_student(class_id, student_id):
    guard = admin_required()
    if guard:
        return guard
    _require_csrf_form()

    pg_remove_student_from_class(str(student_id), str(class_id))
    return redirect(request.referrer or url_for("admin_class_students", class_id=class_id))

@app.route("/admin/classes/<class_id>/instructors")
def admin_class_instructors(class_id):
    guard = admin_required()
    if guard:
        return guard

    cmeta = pg_get_class_by_id(str(class_id))
    if not cmeta:
        return redirect_with_msg("/admin/classes", "Class not found.")

    instructors = pg_list_all_users(role="instructor", limit=5000)
    assigned = pg_list_instructors_in_class(str(class_id), limit=5000)

    return render_template(
        "admin_class_instructors.html",
        class_id=str(class_id),
        class_meta=cmeta,
        instructors=instructors,
        assigned=assigned,
    )

@app.route("/admin/classes/<class_id>/instructors/add", methods=["POST"])
def admin_add_instructor(class_id):
    guard = admin_required()
    if guard:
        return guard
    _require_csrf_form()

    instructor_id = (request.form.get("instructor_id") or "").strip()
    if not instructor_id:
        return redirect(request.referrer or url_for("admin_class_instructors", class_id=class_id))

    try:
        pg_assign_instructor_to_class(str(instructor_id), str(class_id))
    except Exception as e:
        return redirect_with_msg(
            request.referrer or url_for("admin_class_instructors", class_id=class_id),
            f"Failed to assign instructor: {str(e)}"
        )

    return redirect(request.referrer or url_for("admin_class_instructors", class_id=class_id))

@app.route("/admin/classes/<class_id>/instructors/remove/<instructor_id>", methods=["POST"])
def admin_remove_instructor(class_id, instructor_id):
    guard = admin_required()
    if guard:
        return guard
    _require_csrf_form()

    pg_remove_instructor_from_class(str(instructor_id), str(class_id))
    return redirect(request.referrer or url_for("admin_class_instructors", class_id=class_id))

@app.route("/api/admins", methods=["POST"])
def api_admins_create():
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
        fb_user = fb_auth.create_user(
            email=email,
            password=password,
            display_name=f"{first_name} {last_name}".strip(),
        )

        firebase_uid = fb_user.uid
        full_name = f"{first_name} {last_name}".strip()

        # Do not use pg_create_or_update_user_profile here because that helper
        # intentionally restricts public registration roles to student/instructor.
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (firebase_uid, first_name, last_name, full_name, email, role)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (firebase_uid) DO UPDATE
                  SET first_name=EXCLUDED.first_name,
                      last_name=EXCLUDED.last_name,
                      full_name=EXCLUDED.full_name,
                      email=EXCLUDED.email,
                      role=EXCLUDED.role
                RETURNING id;
                """,
                (firebase_uid, first_name, last_name, full_name, email, "admin"),
            )
            row = cur.fetchone()
            admin_id = row["id"] if row and "id" in row else None
            conn.commit()

        return jsonify({
            "success": True,
            "message": "Admin created successfully",
            "data": {
                "id": str(admin_id) if admin_id else None,
                "firebase_uid": firebase_uid,
            },
        }), 201

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Failed to create admin: {str(e)}"
        }), 500

@app.route("/api/admins/<admin_id>", methods=["DELETE"])
def api_admins_delete(admin_id):
    guard = admin_required()
    if guard:
        return jsonify({"success": False, "error": "Admin access only"}), 403

    current_user_id = str(session.get("user_id") or "")
    if str(admin_id) == current_user_id:
        return jsonify({
            "success": False,
            "error": "You cannot delete your own account"
        }), 400

    row = pg_find_user_by_pg_id(str(admin_id)) or pg_find_user_by_firebase_uid(str(admin_id))
    if not row:
        return jsonify({
            "success": False,
            "error": "Admin not found"
        }), 404

    role = row.get("role", "") if isinstance(row, dict) else getattr(row, "role", "")
    if role != "admin":
        return jsonify({
            "success": False,
            "error": "Selected user is not an admin"
        }), 400

    real_admin_id = str(row["id"] if isinstance(row, dict) else getattr(row, "id"))

    if real_admin_id == current_user_id:
        return jsonify({
            "success": False,
            "error": "You cannot delete your own account"
        }), 400

    try:
        deleted = pg_delete_user(real_admin_id)

        if not deleted:
            return jsonify({
                "success": False,
                "error": "Admin delete failed"
            }), 404

        return jsonify({
            "success": True,
            "message": "Admin deleted successfully"
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Failed to delete admin: {str(e)}"
        }), 500


# ============================================================
# ADMIN ATTENDANCE ANALYTICS API
# Provides present/late/absent summaries for admin dashboard charts.
# ============================================================
@app.route("/api/admin/attendance-analytics", methods=["GET"])
def api_admin_attendance_analytics():
    guard = admin_required()
    if guard:
        return jsonify({
            "ok": False,
            "error": "Admin access only",
        }), 403

    try:
        class_id = (request.args.get("class_id") or "").strip()
        date_filter = (request.args.get("date") or "").strip()

        where_parts = []
        params = []

        if class_id:
            where_parts.append("ar.class_id = %s")
            params.append(class_id)

        if date_filter:
            where_parts.append("ar.attendance_date = %s")
            params.append(date_filter)

        where_sql = "WHERE " + " AND ".join(where_parts) if where_parts else ""

        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS total_records,
                    COUNT(*) FILTER (WHERE LOWER(COALESCE(ar.status, '')) = 'present') AS present_count,
                    COUNT(*) FILTER (WHERE LOWER(COALESCE(ar.status, '')) = 'late') AS late_count,
                    COUNT(*) FILTER (WHERE LOWER(COALESCE(ar.status, '')) = 'absent') AS absent_count
                FROM attendance_records ar
                {where_sql};
                """,
                params,
            )
            summary = cur.fetchone() or {}

            cur.execute(
                f"""
                SELECT
                    ar.attendance_date,
                    COUNT(*) AS total_records,
                    COUNT(*) FILTER (WHERE LOWER(COALESCE(ar.status, '')) = 'present') AS present_count,
                    COUNT(*) FILTER (WHERE LOWER(COALESCE(ar.status, '')) = 'late') AS late_count,
                    COUNT(*) FILTER (WHERE LOWER(COALESCE(ar.status, '')) = 'absent') AS absent_count
                FROM attendance_records ar
                {where_sql}
                GROUP BY ar.attendance_date
                ORDER BY ar.attendance_date DESC
                LIMIT 30;
                """,
                params,
            )
            by_date = cur.fetchall() or []

            cur.execute(
                f"""
                SELECT
                    c.id AS class_id,
                    c.class_code,
                    c.section_name,
                    c.subject,
                    COUNT(ar.id) AS total_records,
                    COUNT(ar.id) FILTER (WHERE LOWER(COALESCE(ar.status, '')) = 'present') AS present_count,
                    COUNT(ar.id) FILTER (WHERE LOWER(COALESCE(ar.status, '')) = 'late') AS late_count,
                    COUNT(ar.id) FILTER (WHERE LOWER(COALESCE(ar.status, '')) = 'absent') AS absent_count
                FROM classes c
                LEFT JOIN attendance_records ar
                    ON ar.class_id = c.id
                {('WHERE ' + ' AND '.join([p.replace('ar.', 'ar.') for p in where_parts])) if where_parts else ''}
                GROUP BY c.id, c.class_code, c.section_name, c.subject
                ORDER BY c.class_code ASC, c.section_name ASC;
                """,
                params,
            )
            by_class = cur.fetchall() or []

        total_records = int(summary.get("total_records") or 0)
        present_count = int(summary.get("present_count") or 0)
        late_count = int(summary.get("late_count") or 0)
        absent_count = int(summary.get("absent_count") or 0)
        attendance_rate = int(round(((present_count + late_count) / total_records) * 100)) if total_records else 0

        return jsonify({
            "ok": True,
            "summary": {
                "total_records": total_records,
                "present_count": present_count,
                "late_count": late_count,
                "absent_count": absent_count,
                "attendance_rate": attendance_rate,
            },
            "by_date": by_date,
            "by_class": by_class,
        })

    except Exception as err:
        print(f"❌ /api/admin/attendance-analytics error: {err}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error": str(err),
        }), 500

# ============================================================
# ADMIN VIOLATION ANALYTICS API
# Provides:
# - violation summaries
# - violation counts
# - recent monitoring violations
# Used by:
# - admin dashboard
# - analytics charts
# - monitoring reports
# ============================================================
@app.route("/api/admin/violation-analytics", methods=["GET"])
def api_admin_violation_analytics():
    guard = admin_required()
    if guard:
        return jsonify({
            "ok": False,
            "error": "Admin access only",
        }), 403

    try:
        pg_ensure_violation_table()

        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:

            # ------------------------------------------------
            # VIOLATION COUNTS BY TYPE
            # ------------------------------------------------
            cur.execute("""
                SELECT
                    violation_type,
                    COUNT(*) AS total
                FROM quiz_attempt_violations
                GROUP BY violation_type
                ORDER BY total DESC;
            """)
            violation_rows = cur.fetchall() or []

            # ------------------------------------------------
            # RECENT VIOLATIONS
            # ------------------------------------------------
            cur.execute("""
                SELECT
                    qv.violation_type,
                    qv.timestamp_iso,
                    qv.time_remaining,
                    COALESCE(
                        u.full_name,
                        CONCAT(u.first_name, ' ', u.last_name),
                        'Unknown Student'
                    ) AS student_name,
                    COALESCE(q.title, 'Unknown Quiz') AS quiz_title
                FROM quiz_attempt_violations qv
                LEFT JOIN quiz_attempts qa
                    ON qa.attempt_id = qv.attempt_id
                LEFT JOIN users u
                    ON qa.user_id = u.id
                LEFT JOIN quizzes q
                    ON qa.quiz_id = q.id
                ORDER BY qv.timestamp_iso DESC
                LIMIT 50;
            """)
            recent_rows = cur.fetchall() or []

        # ----------------------------------------------------
        # FORMAT COUNTS
        # ----------------------------------------------------
        by_type = []

        for row in violation_rows:
            violation_type = str(row.get("violation_type") or "")

            by_type.append({
                "violation_type": violation_type,
                "label": human_violation_label(violation_type),
                "total": int(row.get("total") or 0),
            })

        # ----------------------------------------------------
        # FORMAT RECENT
        # ----------------------------------------------------
        recent = []

        for row in recent_rows:
            violation_type = str(row.get("violation_type") or "")

            recent.append({
                "student_name": row.get("student_name"),
                "quiz_title": row.get("quiz_title"),
                "violation_type": violation_type,
                "label": human_violation_label(violation_type),
                "timestamp_iso": row.get("timestamp_iso"),
                "time_remaining": row.get("time_remaining"),
            })

        total_violations = sum(item["total"] for item in by_type)

        return jsonify({
            "ok": True,
            "summary": {
                "total_violations": total_violations,
                "unique_violation_types": len(by_type),
            },
            "by_type": by_type,
            "recent": recent,
        })

    except Exception as err:
        print(
            f"❌ /api/admin/violation-analytics error: {err}",
            flush=True,
        )

        import traceback
        traceback.print_exc()

        return jsonify({
            "ok": False,
            "error": str(err),
        }), 500
